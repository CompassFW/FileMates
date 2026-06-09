// macos-ocr.swift — add a searchable, invisible OCR text layer to a PDF using Apple's
// built-in Vision OCR (no Homebrew/tesseract needed). macOS only.
//
// Usage:  swift macos-ocr.swift <in.pdf> <out.pdf> [langs]
//         langs: comma-separated BCP-47, e.g. "de-DE,en-US" (default).
//         If <out.pdf> == <in.pdf> (or "-"), it OCRs IN PLACE — but only swaps the original
//         after a temp file in the SAME directory is fully written AND text was actually added
//         AND every page was preserved. On any doubt the original is left untouched.
//
// Per page: render the original (honouring /Rotate); if the OCR text is poor, AUTO-DETECT the
// page orientation by trying 0/90/180/270 and keeping the rotation with the most recognised
// text (fixes sideways scans — they become upright AND searchable). Upright pages keep their
// original VECTOR content; rotation-corrected pages are written as the de-rotated image.
// Recognised text is overlaid INVISIBLE at its bounding boxes, so the file is fully searchable.

import Foundation
import Vision
import PDFKit
import CoreGraphics
import CoreText
import AppKit

func die(_ msg: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
guard args.count >= 3 else { die("usage: macos-ocr <in.pdf> <out.pdf> [langs]", 2) }
let inPath = args[1]
let outArg = args[2]
let langs = args.count > 3 ? args[3].split(separator: ",").map(String.init) : ["de-DE", "en-US"]

let inURL = URL(fileURLWithPath: inPath)
guard let doc = PDFDocument(url: inURL) else { die("cannot open PDF: \(inPath)") }
let pageCount = doc.pageCount
guard pageCount > 0 else { die("empty PDF: \(inPath)") }

let inPlace = (outArg == inPath) || (outArg == "-")
let finalURL = URL(fileURLWithPath: inPlace ? inPath : outArg)
let dir = finalURL.deletingLastPathComponent()
let writeURL = dir.appendingPathComponent(".ocr-\(ProcessInfo.processInfo.globallyUniqueString).pdf")
func cleanupTemp() { try? FileManager.default.removeItem(at: writeURL) }

guard let consumer = CGDataConsumer(url: writeURL as CFURL) else { die("cannot write: \(writeURL.path)") }
var mediaBox = CGRect.zero
guard let ctx = CGContext(consumer: consumer, mediaBox: &mediaBox, nil) else {
    cleanupTemp(); die("cannot create PDF context")
}

// --- helpers ---
func ocrImage(_ img: CGImage) -> [VNRecognizedTextObservation] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = true
    req.recognitionLanguages = langs
    let h = VNImageRequestHandler(cgImage: img, options: [:])
    do { try h.perform([req]) } catch { return [] }
    return req.results ?? []
}
func charCount(_ obs: [VNRecognizedTextObservation]) -> Int {
    obs.reduce(0) { $0 + ($1.topCandidates(1).first.map { $0.string.count } ?? 0) }
}
func rotatedImage(_ img: CGImage, _ deg: Int) -> CGImage? {
    if deg % 360 == 0 { return img }
    let w = img.width, h = img.height
    let swap = (deg == 90 || deg == 270)
    let nw = swap ? h : w, nh = swap ? w : h
    guard let c = CGContext(data: nil, width: nw, height: nh, bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpaceCreateDeviceRGB(),
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
    c.translateBy(x: CGFloat(nw) / 2, y: CGFloat(nh) / 2)
    c.rotate(by: CGFloat(Double(deg) * Double.pi / 180))
    c.translateBy(x: -CGFloat(w) / 2, y: -CGFloat(h) / 2)
    c.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))
    return c.makeImage()
}
func overlayInvisible(_ obs: [VNRecognizedTextObservation], _ W: CGFloat, _ H: CGFloat) {
    ctx.saveGState()
    ctx.setTextDrawingMode(.invisible)
    for o in obs {
        guard let cand = o.topCandidates(1).first, !cand.string.isEmpty else { continue }
        let s = cand.string
        let bb = o.boundingBox
        let rect = CGRect(x: bb.origin.x * W, y: bb.origin.y * H, width: bb.size.width * W, height: bb.size.height * H)
        if rect.height <= 0 || rect.width <= 0 { continue }
        let font = CTFontCreateWithName("Helvetica" as CFString, max(1.0, rect.height * 0.9), nil)
        let line = CTLineCreateWithAttributedString(NSAttributedString(string: s, attributes: [.font: font]))
        let lineW = CGFloat(CTLineGetTypographicBounds(line, nil, nil, nil))
        var tx = CGAffineTransform.identity
        if lineW > rect.width, rect.width > 0 { tx = tx.scaledBy(x: rect.width / lineW, y: 1.0) }
        ctx.textMatrix = tx
        ctx.textPosition = CGPoint(x: rect.origin.x, y: rect.origin.y + rect.height * 0.18)
        CTLineDraw(line, ctx)
    }
    ctx.restoreGState()
}

let ROTATE_RETRY_THRESHOLD = 30   // if the upright OCR yields fewer chars, try other orientations

var totalChars = 0
var pagesDrawn = 0

for i in 0..<pageCount {
    autoreleasepool {
        guard let page = doc.page(at: i) else { return }      // pagesDrawn < pageCount -> abort below
        let rot = ((page.rotation % 360) + 360) % 360
        let raw = page.bounds(for: .mediaBox)
        let w0 = (rot == 90 || rot == 270) ? raw.height : raw.width
        let h0 = (rot == 90 || rot == 270) ? raw.width : raw.height
        if w0 <= 0 || h0 <= 0 { return }

        // rasterise the page upright-per-/Rotate
        var scale: CGFloat = 300.0 / 72.0
        let maxSide: CGFloat = 5000
        if max(w0, h0) * scale > maxSide { scale = maxSide / max(w0, h0) }
        let pxW = Int(w0 * scale), pxH = Int(h0 * scale)
        guard pxW > 0, pxH > 0,
              let bmp = CGContext(data: nil, width: pxW, height: pxH, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            // can't rasterise -> still preserve the page as vector, no text layer
            var box = CGRect(x: 0, y: 0, width: w0, height: h0); ctx.beginPage(mediaBox: &box)
            ctx.saveGState(); page.draw(with: .mediaBox, to: ctx); ctx.restoreGState()
            ctx.endPage(); pagesDrawn += 1; return
        }
        bmp.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        bmp.fill(CGRect(x: 0, y: 0, width: pxW, height: pxH))
        bmp.scaleBy(x: scale, y: scale)
        page.draw(with: .mediaBox, to: bmp)
        guard let baseImg = bmp.makeImage() else {
            var box = CGRect(x: 0, y: 0, width: w0, height: h0); ctx.beginPage(mediaBox: &box)
            ctx.saveGState(); page.draw(with: .mediaBox, to: ctx); ctx.restoreGState()
            ctx.endPage(); pagesDrawn += 1; return
        }

        // OCR upright; if poor, auto-detect the best of 90/180/270 — but only ADOPT a rotation
        // if it CLEARLY beats upright, so a correct upright (vector) page is never needlessly
        // rasterised just because a rotation hallucinated a few more chars from noise.
        var bestDeg = 0
        var bestObs = ocrImage(baseImg)
        var bestCount = charCount(bestObs)
        if bestCount < ROTATE_RETRY_THRESHOLD {
            var rDeg = 0, rCount = -1
            var rObs: [VNRecognizedTextObservation] = []
            for d in [90, 180, 270] {
                autoreleasepool {
                    guard let r = rotatedImage(baseImg, d) else { return }
                    let o = ocrImage(r); let c = charCount(o)
                    if c > rCount { rCount = c; rObs = o; rDeg = d }
                }
            }
            if rCount > bestCount + 20 && rCount > Int(Double(max(bestCount, 1)) * 1.5) {
                bestDeg = rDeg; bestObs = rObs; bestCount = rCount
            }
        }
        totalChars += bestCount

        if bestDeg == 0 {
            // upright: keep original VECTOR content + invisible text
            var box = CGRect(x: 0, y: 0, width: w0, height: h0); ctx.beginPage(mediaBox: &box)
            ctx.saveGState(); page.draw(with: .mediaBox, to: ctx); ctx.restoreGState()
            overlayInvisible(bestObs, w0, h0)
            ctx.endPage(); pagesDrawn += 1
        } else {
            // rotation-corrected: write the de-rotated image as the page + invisible text
            let swap = (bestDeg == 90 || bestDeg == 270)
            let W = swap ? h0 : w0, H = swap ? w0 : h0
            guard let upImg = rotatedImage(baseImg, bestDeg) else {
                var box = CGRect(x: 0, y: 0, width: w0, height: h0); ctx.beginPage(mediaBox: &box)
                ctx.saveGState(); page.draw(with: .mediaBox, to: ctx); ctx.restoreGState()
                ctx.endPage(); pagesDrawn += 1; return
            }
            var box = CGRect(x: 0, y: 0, width: W, height: H); ctx.beginPage(mediaBox: &box)
            ctx.draw(upImg, in: CGRect(x: 0, y: 0, width: W, height: H))
            overlayInvisible(bestObs, W, H)
            ctx.endPage(); pagesDrawn += 1
        }
    }
}

ctx.closePDF()

if pagesDrawn != pageCount {
    cleanupTemp()
    die("OCR aborted: only \(pagesDrawn)/\(pageCount) pages rendered — original left untouched.")
}
// Re-validate the WRITTEN temp (not just in-memory counters): catches a truncated/partial
// write (e.g. disk full during closePDF) before it could replace the original.
guard let verify = PDFDocument(url: writeURL), verify.pageCount == pageCount else {
    cleanupTemp()
    die("OCR output failed validation (unreadable or page-count mismatch) — original left untouched.")
}
if inPlace && totalChars == 0 {
    cleanupTemp()
    die("OCR added no text (totalChars=0) — original left untouched (probably not a scan / no recognisable text).")
}

if inPlace {
    do { _ = try FileManager.default.replaceItemAt(finalURL, withItemAt: writeURL) }
    catch { die("could not replace original (left intact). OCR output kept at: \(writeURL.path)\n\(error)") }
} else {
    do {
        if FileManager.default.fileExists(atPath: finalURL.path) {
            _ = try FileManager.default.replaceItemAt(finalURL, withItemAt: writeURL)
        } else {
            try FileManager.default.moveItem(at: writeURL, to: finalURL)
        }
    } catch { cleanupTemp(); die("could not write output \(finalURL.path): \(error)") }
}

print("OCR done: \(pageCount) page(s), ~\(totalChars) chars -> \(finalURL.path)")
