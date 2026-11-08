from pypdf import PdfReader

def parse_pdf(path):
    reader = PdfReader(path)
    data = []

    for page_number, page in enumerate(reader.pages, start=1):
        page_data = {
            "file_name": path.name,
            "page": page_number,
            "text": page.extract_text(extraction_mode="layout") or ""
        }
        data.append(page_data)

    return data


def restore_paragraph(block):
    restored = ""
    for raw_line in block.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue

        if restored.endswith((".", ",", ")", "?", "!", ":", "”", "’")):
            restored += " "
        restored += line

    return restored
