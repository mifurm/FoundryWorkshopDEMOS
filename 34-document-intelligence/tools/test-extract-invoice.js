"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const handler = require("./extract-invoice");
const { invoicePdf, normalize, analyze } = handler;
const { renderPdf } = require("./generate-samples");
const samples = require("../samples/manifest.json");
const pdfs = Object.fromEntries(Object.keys(samples).map(id => [
    id, fs.readFileSync(path.join(__dirname, "../samples", `${id}.pdf`)).toString("base64"),
]));

const field = (valueString, content = valueString) => ({
    valueString, content, confidence: 0.97, boundingRegions: [{ pageNumber: 1 }],
});
const result = () => ({
    modelId: "prebuilt-invoice", pages: [{}], documents: [{ fields: {
        VendorName: field("Synthetic Workshop Supplies"), InvoiceId: field("LAB-3401"),
        InvoiceTotal: { ...field(undefined, "USD $125.50"), valueCurrency: { amount: 125.5, currencyCode: "USD" } },
    } }],
});
test("all twelve stored PDFs are reproducible and have valid xref offsets", () => {
    assert.equal(Object.keys(samples).length, 12);
    for (const [id, sample] of Object.entries(samples)) {
        const stored = invoicePdf(id, pdfs);
        assert.deepEqual(stored, renderPdf(sample));
        const pdf = stored.toString();
        assert.ok(pdf.startsWith("%PDF-1.4"));
        const offset = Number(pdf.match(/startxref\n(\d+)/)[1]);
        assert.ok(pdf.slice(offset).startsWith("xref\n"));
        for (const match of pdf.matchAll(/(\d{10}) 00000 n/g)) {
            assert.match(pdf.slice(Number(match[1])), /^\d+ 0 obj/);
        }
        assert.equal(pdf.includes("Total: NOT PROVIDED"), sample.total === null);
    }
    assert.throws(() => invoicePdf("https://untrusted.example", pdfs), /Unknown/);
    assert.throws(() => invoicePdf("__proto__", pdfs), /Unknown/);
    assert.throws(() => invoicePdf("clear", { clear: Buffer.from("not PDF").toString("base64") }), /not a valid/);
});
test("normalization preserves service evidence, missing fields and low confidence", () => {
    const raw = result();
    assert.equal(normalize(raw, "clear", "op").needs_review, false);
    raw.documents[0].fields.VendorName.confidence = 0.79;
    assert.equal(normalize(raw, "clear", "op").needs_review, true);
    delete raw.documents[0].fields.InvoiceTotal;
    const output = normalize(raw, "missing-total", "op");
    assert.equal(output.fields.total.value, null);
    assert.equal(output.currency, null);
    assert.equal(output.needs_review, true);
    assert.throws(() => normalize({ ...raw, documents: [] }, "clear", "op"), /Expected/);
});
test("real REST flow uses managed identity, PDF submission and bounded polling", async () => {
    process.env.DOCUMENT_INTELLIGENCE_ENDPOINT = "https://docs.cognitiveservices.azure.com/";
    process.env.IDENTITY_ENDPOINT = "http://localhost/identity";
    process.env.IDENTITY_HEADER = "test-only";
    const requests = [];
    const replies = [
        new Response(JSON.stringify({ access_token: "test-token" })),
        new Response(null, { status: 202, headers: {
            "operation-location": "https://docs.cognitiveservices.azure.com/documentintelligence/documentModels/prebuilt-invoice/analyzeResults/op?api-version=2024-11-30",
        } }),
        new Response(JSON.stringify({ status: "running" })),
        new Response(JSON.stringify({ status: "succeeded", analyzeResult: result() })),
    ];
    const fakeFetch = async (url, options) => { requests.push([String(url), options]); return replies.shift(); };
    const output = await analyze("clear", fakeFetch, async () => {}, pdfs);
    assert.equal(output.operation_id, "op");
    assert.equal(requests.length, 4);
    assert.equal(requests[1][1].headers.Authorization, "Bearer test-token");
    assert.ok(Buffer.isBuffer(requests[1][1].body));
    assert.deepEqual(requests[1][1].body, invoicePdf("clear", pdfs));
    assert.equal(output.document_sha256, createHash("sha256").update(requests[1][1].body).digest("hex"));
    assert.match(requests[1][0], /prebuilt-invoice:analyze\?api-version=2024-11-30$/);
});
test("upstream failures, unsafe operation URLs and polling exhaustion fail explicitly", async () => {
    for (const mode of ["submission", "unsafe", "failed", "timeout"]) {
        let calls = 0;
        const fetcher = async () => {
            calls++;
            if (calls === 1) return new Response(JSON.stringify({ access_token: "token" }));
            if (calls === 2) return new Response(null, {
                status: mode === "submission" ? 403 : 202,
                headers: { "operation-location": mode === "unsafe" ? "https://evil.example/steal"
                    : "https://docs.cognitiveservices.azure.com/documentintelligence/documentModels/prebuilt-invoice/analyzeResults/op" },
            });
            return new Response(JSON.stringify({ status: mode === "failed" ? "failed" : "running" }));
        };
        await assert.rejects(() => analyze("clear", fetcher, async () => {}, pdfs),
            mode === "submission" ? /HTTP 403/ : mode === "unsafe" ? /unexpected operation URL/
                : mode === "failed" ? /analysis failed/ : /35-poll budget/);
        assert.ok(calls <= 37);
    }
});
test("unknown document is a logged 400 without any cloud call", async t => {
    t.mock.method(fs, "readFileSync", () => "\uFEFF" + JSON.stringify(pdfs));
    const logs = [];
    const context = { log: { warn: text => logs.push(text) } };
    await handler(context, { params: { document_id: "bad" } });
    assert.equal(context.res.status, 400);
    assert.equal(logs.length, 1);
});
test("configuration failure is logged and returns 502, never a successful extraction", async t => {
    t.mock.method(fs, "readFileSync", () => JSON.stringify(pdfs));
    const previous = process.env.IDENTITY_HEADER;
    delete process.env.IDENTITY_HEADER;
    try {
        const logs = [];
        const context = { log: { error: (...args) => logs.push(args) } };
        await handler(context, { params: { document_id: "clear" } });
        assert.equal(context.res.status, 502);
        assert.equal(logs.length, 1);
        assert.match(context.res.body.error, /extraction failed/);
        assert.equal(context.res.body.fields, undefined);
    } finally {
        process.env.IDENTITY_HEADER = previous;
    }
});
test("missing or corrupted deployed corpus is a logged 502", async t => {
    t.mock.method(fs, "readFileSync", () => { throw new Error("Missing documents.json"); });
    const logs = [];
    const context = { log: { error: (...args) => logs.push(args) } };
    await handler(context, { params: { document_id: "clear" } });
    assert.equal(context.res.status, 502);
    assert.equal(logs.length, 1);
});
