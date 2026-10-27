"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const API_VERSION = "2024-11-30";
const SOURCE = "document-intelligence-prebuilt-invoice-v1";
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function loadPdfs() {
    // ARM-authored Windows Function files can include a UTF-8 BOM.
    return JSON.parse(fs.readFileSync(path.join(__dirname, "documents.json"), "utf8").replace(/^\uFEFF/, ""));
}

function invoicePdf(documentId, pdfs = loadPdfs()) {
    if (!Object.hasOwn(pdfs, documentId)) {
        throw new Error("Unknown synthetic document ID.");
    }
    const pdf = Buffer.from(pdfs[documentId], "base64");
    if (!pdf.subarray(0, 8).equals(Buffer.from("%PDF-1.4"))) {
        throw new Error("Deployed document is not a valid workshop PDF.");
    }
    return pdf;
}

async function jsonResponse(response, stage) {
    if (!response.ok) {
        throw new Error(`${stage} failed with HTTP ${response.status}: ${(await response.text()).slice(0, 1000)}`);
    }
    return response.json();
}

function normalize(result, documentId, operationId) {
    if (result.modelId !== "prebuilt-invoice" || result.documents?.length !== 1 || result.pages?.length !== 1) {
        throw new Error("Expected one page and one invoice from prebuilt-invoice.");
    }
    const fields = result.documents[0].fields;
    const read = (name, value) => {
        const field = fields?.[name];
        const extracted = value(field);
        return {
            value: extracted ?? null,
            confidence: typeof field?.confidence === "number" ? field.confidence : 0,
            evidence: field?.content ?? null,
            page: field?.boundingRegions?.[0]?.pageNumber ?? null,
        };
    };
    const total = fields?.InvoiceTotal;
    const output = {
        source: SOURCE, document_id: documentId, model_id: result.modelId,
        operation_id: operationId, status: "succeeded", page_count: result.pages.length,
        fields: {
            vendor: read("VendorName", field => field?.valueString),
            invoice_id: read("InvoiceId", field => field?.valueString),
            total: read("InvoiceTotal", field => field?.valueCurrency?.amount),
        },
        currency: total?.valueCurrency?.currencyCode ?? null,
    };
    output.needs_review = Object.values(output.fields).some(
        field => field.value === null || field.confidence < 0.8) || output.currency === null;
    return output;
}

async function analyze(documentId, fetcher = fetch, wait = sleep, pdfs = loadPdfs()) {
    const pdf = invoicePdf(documentId, pdfs);
    const endpoint = process.env.DOCUMENT_INTELLIGENCE_ENDPOINT;
    if (!endpoint?.startsWith("https://") || !process.env.IDENTITY_ENDPOINT || !process.env.IDENTITY_HEADER) {
        throw new Error("Document Intelligence endpoint and Function managed identity are required.");
    }
    const identity = new URL(process.env.IDENTITY_ENDPOINT);
    identity.searchParams.set("resource", "https://cognitiveservices.azure.com");
    identity.searchParams.set("api-version", "2019-08-01");
    const token = await jsonResponse(await fetcher(identity, {
        headers: { "X-IDENTITY-HEADER": process.env.IDENTITY_HEADER },
        signal: AbortSignal.timeout(10000),
    }), "Managed identity token");
    if (!token.access_token) throw new Error("Managed identity returned no access token.");
    const headers = { Authorization: `Bearer ${token.access_token}` };
    const submitted = await fetcher(
        `${endpoint.replace(/\/$/, "")}/documentintelligence/documentModels/prebuilt-invoice:analyze?api-version=${API_VERSION}`,
        { method: "POST", headers: { ...headers, "Content-Type": "application/pdf" },
            body: pdf, signal: AbortSignal.timeout(15000) },
    );
    if (submitted.status !== 202) {
        throw new Error(`Document submission failed with HTTP ${submitted.status}: ${(await submitted.text()).slice(0, 1000)}`);
    }
    const operation = submitted.headers.get("operation-location");
    const url = new URL(operation);
    if (url.origin !== new URL(endpoint).origin ||
        !url.pathname.startsWith("/documentintelligence/documentModels/prebuilt-invoice/analyzeResults/")) {
        throw new Error("Document Intelligence returned an unexpected operation URL.");
    }
    for (let attempt = 0; attempt < 35; attempt++) {
        const result = await jsonResponse(await fetcher(url, {
            headers, signal: AbortSignal.timeout(10000),
        }), "Document analysis polling");
        if (result.status === "succeeded") {
            return {
                ...normalize(result.analyzeResult, documentId, url.pathname.split("/").pop()),
                document_sha256: createHash("sha256").update(pdf).digest("hex"),
            };
        }
        if (!["notStarted", "running"].includes(result.status)) {
            throw new Error(`Document analysis failed: ${JSON.stringify(result.error ?? result)}`);
        }
        await wait(1000);
    }
    throw new Error("Document analysis exceeded the 35-poll budget.");
}

module.exports = async function (context, req) {
    const documentId = req.params?.document_id;
    try {
        const pdfs = loadPdfs();
        if (!Object.hasOwn(pdfs, documentId)) {
            context.log.warn("Rejected unknown synthetic document ID.");
            context.res = { status: 400, body: { error: "document_id must identify an approved sample PDF." } };
            return;
        }
        context.res = { status: 200, headers: { "Content-Type": "application/json" },
            body: await analyze(documentId, fetch, sleep, pdfs) };
    } catch (error) {
        context.log.error("Document Intelligence extraction failed.", error);
        context.res = { status: 502, body: { error: "Document Intelligence extraction failed; inspect Function logs." } };
    }
};
module.exports.invoicePdf = invoicePdf;
module.exports.normalize = normalize;
module.exports.analyze = analyze;
