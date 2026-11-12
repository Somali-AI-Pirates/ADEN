"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import type { Citation } from "../lib/api";
import { documentUrl } from "../lib/api";

pdfjs.GlobalWorkerOptions.workerSrc = "/pdf-assets/pdf.worker.min.mjs";
const options = {
  cMapUrl: "/pdf-assets/cmaps/",
  cMapPacked: true,
  standardFontDataUrl: "/pdf-assets/standard_fonts/",
  wasmUrl: "/pdf-assets/wasm/",
};

export default function PdfPanel({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  const [page, setPage] = useState(citation.page);
  const [pageInput, setPageInput] = useState(String(citation.page));
  const [count, setCount] = useState(0);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [error, setError] = useState("");
  const viewport = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setPage(citation.page);
    setPageInput(String(citation.page));
    setError("");
    viewport.current?.scrollTo(0, 0);
    closeButton.current?.focus();
  }, [citation]);

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const url = documentUrl(citation);
  const pageOutOfRange = count > 0 && page > count;
  const documentError = pageOutOfRange ? "출처의 페이지가 원본 문서 범위를 벗어났습니다." : error;

  function navigate(next: number) {
    if (!Number.isInteger(next) || next < 1 || next > count) {
      setPageInput(String(page));
      return;
    }
    setPageInput(String(next));
    if (next !== page) {
      setPage(next);
      viewport.current?.scrollTo(0, 0);
    }
  }

  return (
    <aside
      className="pdf-panel"
      aria-label="원본 문서"
      onKeyDown={event => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <header className="pdf-header">
        <div className="pdf-title">
          <p>원본 문서</p>
          <h2 title={citation.file_name}>{citation.file_name}</h2>
        </div>
        <button ref={closeButton} className="icon-button" onClick={onClose} aria-label="문서 닫기">×</button>
      </header>
      <div className="pdf-toolbar">
        <form className="pdf-pagination" noValidate onSubmit={event => {
          event.preventDefault();
          navigate(Number(pageInput));
        }}>
          <button type="button" className="quiet-button" disabled={!count || page <= 1} onClick={() => navigate(page - 1)} aria-label="이전 페이지">←</button>
          <input
            aria-label="페이지"
            type="number"
            inputMode="numeric"
            min={1}
            max={count || undefined}
            disabled={!count}
            value={pageInput}
            onChange={event => setPageInput(event.target.value)}
            onBlur={() => navigate(Number(pageInput))}
          />
          <span>/ {count || "…"}</span>
          <button type="button" className="quiet-button" disabled={!count || page >= count} onClick={() => navigate(page + 1)} aria-label="다음 페이지">→</button>
        </form>
        <div className="pdf-tools">
          <select aria-label="문서 확대" value={zoom} onChange={event => setZoom(Number(event.target.value))}>
            <option value={1}>너비 맞춤</option>
            <option value={1.25}>125%</option>
            <option value={1.5}>150%</option>
          </select>
          <a className="quiet-button" href={`${url}#page=${page}`} target="_blank" rel="noopener noreferrer" aria-label="새 탭에서 원본 열기">
            <span className="new-tab-label">새 탭</span><span aria-hidden="true">↗</span>
          </a>
        </div>
      </div>
      <div className="pdf-viewport" ref={viewport}>
        {documentError ? (
          <div className="pdf-error" role="alert">
            <strong>문서를 열지 못했습니다.</strong>
            <p>{documentError}</p>
            <a href={url} target="_blank" rel="noopener noreferrer">원본 직접 열기</a>
          </div>
        ) : (
          <Suspense fallback={<p className="pdf-loading">문서를 불러오고 있습니다…</p>}>
            <Document
              className="pdf-document"
              file={url}
              options={options}
              suspense={false}
              loading={<p className="pdf-loading">문서를 불러오고 있습니다…</p>}
              onLoadSuccess={({ numPages }) => setCount(numPages)}
              onLoadError={() => setError("원본 PDF가 준비돼 있는지 확인해 주세요.")}
            >
              {width > 0 && (
                <Page
                  pageNumber={page}
                  width={width * zoom}
                  suspense={false}
                  onRenderError={() => setError("페이지를 표시하지 못했습니다. 원본을 새 탭에서 열어 주세요.")}
                  loading={<p className="pdf-loading">{page}쪽을 표시하고 있습니다…</p>}
                />
              )}
            </Document>
          </Suspense>
        )}
      </div>
      <div className="pdf-caption">
        <span>출처 {citation.page}쪽</span>
        <button className="quiet-button" disabled={page === citation.page} onClick={() => navigate(citation.page)}>출처로 돌아가기</button>
      </div>
    </aside>
  );
}
