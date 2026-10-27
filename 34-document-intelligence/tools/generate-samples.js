"use strict";

const fs = require("node:fs");
const path = require("node:path");
const samples = require("../samples/manifest.json");

function renderPdf(sample) {
    const amount = sample.total === null ? null : sample.total.toFixed(2);
    const unit = sample.currency === "USD" ? "$" : `${sample.currency} `;
    const lines = [
        "INVOICE", sample.vendor, "100 Example Avenue",
        "Sample City, WA 98000", `Invoice Number: ${sample.invoice_id}`,
        "Invoice Date: October 27, 2026", "Bill To: Example Workshop",
        `Description: ${sample.description}`,
        ...(amount !== null ? [
            `Subtotal: ${unit}${amount}`, `Tax: ${unit}0.00`,
            `Invoice Total: ${sample.currency === "USD" ? "USD $" : unit}${amount}`,
        ] : ["Total: NOT PROVIDED"]),
        "SYNTHETIC TRAINING DOCUMENT - NOT PAYABLE",
    ];
    const stream = "BT /F1 18 Tf 50 750 Td " + lines.map((line, i) =>
        `${i ? "0 -36 Td " : ""}(${line.replace(/[\\()]/g, "\\$&")}) Tj`).join("\n") + "\nET";
    const objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
    ];
    let pdf = "%PDF-1.4\n";
    const offsets = [0];
    objects.forEach((object, i) => {
        offsets.push(Buffer.byteLength(pdf));
        pdf += `${i + 1} 0 obj\n${object}\nendobj\n`;
    });
    const xref = Buffer.byteLength(pdf);
    pdf += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(offset =>
        `${String(offset).padStart(10, "0")} 00000 n \n`).join("")}`;
    return Buffer.from(pdf + `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
}

if (require.main === module) {
    for (const [id, sample] of Object.entries(samples)) {
        fs.writeFileSync(path.join(__dirname, "../samples", `${id}.pdf`), renderPdf(sample));
        console.log(`Generated samples/${id}.pdf`);
    }
}

module.exports = { renderPdf };
