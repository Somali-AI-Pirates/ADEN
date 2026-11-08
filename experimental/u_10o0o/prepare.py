import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = "advanced"
DATA_DIR = PROJECT_ROOT / "data" / DATASET
CACHE_PATH = PROJECT_ROOT / "artifacts/u_10o0o" / f"{DATASET}_restored_pages.json"


def main() -> None:
    from experimental.u_10o0o.pdf import parse_pdf, restore_paragraph

    documents_dir = DATA_DIR / "documents"
    pdfs = sorted(documents_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"{DATASET.capitalize()} 원본 PDF가 없습니다. {documents_dir}")

    restored_pages = []
    for pdf in pdfs:
        for page in parse_pdf(pdf):
            blocks = re.split(r"\n\s*\n", page["text"].strip())
            text = "\n\n".join(restore_paragraph(block) for block in blocks)
            restored_pages.append({**page, "text": text})

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=CACHE_PATH.parent) as temporary:
        temporary_file = Path(temporary) / CACHE_PATH.name
        temporary_file.write_text(
            json.dumps(restored_pages, ensure_ascii=False), encoding="utf-8"
        )
        temporary_file.replace(CACHE_PATH)

    print(
        f"{DATASET.capitalize()} {len(restored_pages)}쪽 저장 → "
        f"{CACHE_PATH.relative_to(PROJECT_ROOT)}"
    )


if __name__ == "__main__":
    main()
