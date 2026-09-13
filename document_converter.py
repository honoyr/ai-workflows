import os
import argparse
import json
from google.cloud import documentai_v1 as documentai
from google.api_core.client_options import ClientOptions
from google.protobuf.json_format import MessageToDict

def process_file(file_path, client, processor_name):
    """Sends a PDF to Document AI and returns the Document object."""
    with open(file_path, "rb") as image:
        image_content = image.read()
    raw_document = documentai.RawDocument(content=image_content, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=processor_name, raw_document=raw_document)
    response = client.process_document(request=request)
    return response.document

def parse_blocks(blocks, depth=0):
    """Recursively converts layout blocks to Markdown."""
    md = []
    for block in blocks:
        if "textBlock" in block:
            text = block["textBlock"].get("text", "")
            block_type = block["textBlock"].get("type", "paragraph")
            
            if block_type == "heading-1":
                md.append(f"\n# {text}\n")
            elif block_type == "heading-2":
                md.append(f"\n## {text}\n")
            elif block_type == "heading-3":
                md.append(f"\n### {text}\n")
            else:
                md.append(f"{text}\n\n")
            
            if "blocks" in block["textBlock"]:
                md.append(parse_blocks(block["textBlock"]["blocks"], depth + 1))

        elif "tableBlock" in block:
            table = block["tableBlock"]
            md.append("\n")
            all_rows = []
            if "headerRows" in table:
                all_rows.extend(table["headerRows"])
            if "bodyRows" in table:
                all_rows.extend(table["bodyRows"])
            
            for i, row in enumerate(all_rows):
                row_cells = []
                for cell in row.get("cells", []):
                    cell_text = ""
                    if "blocks" in cell:
                        cell_text = parse_blocks(cell["blocks"], depth + 1).strip().replace("\n", " ")
                    row_cells.append(cell_text)
                md.append("| " + " | ".join(row_cells) + " |\n")
                if i == 0 and "headerRows" in table:
                    md.append("| " + " | ".join(["---"] * len(row_cells)) + " |\n")
            md.append("\n")

        elif "listBlock" in block:
            for item in block["listBlock"].get("listItems", []):
                if "blocks" in item:
                    item_text = parse_blocks(item["blocks"], depth + 1).strip()
                    md.append(f"* {item_text}\n")
            md.append("\n")
    return "".join(md)

def main():
    parser = argparse.ArgumentParser(description="Convert PDFs in a directory to Markdown using Google Document AI.")
    parser.add_argument("source_dir", help="Path to the directory containing PDFs.")
    parser.add_argument("--project", required=True, help="GCP Project ID")
    parser.add_argument("--processor", required=True, help="Document AI Processor ID (Layout Parser)")
    parser.add_argument("--location", default="us", help="GCP Location (default: us)")
    
    args = parser.parse_args()
    
    source_dir = os.path.abspath(args.source_dir)
    target_dir = os.path.join(source_dir, "markdown_google")
    
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    opts = ClientOptions(api_endpoint=f"{args.location}-documentai.googleapis.com")
    client = documentai.DocumentProcessorServiceClient(client_options=opts)
    processor_name = client.processor_path(args.project, args.location, args.processor)

    print(f"--- Starting conversion in {source_dir} ---")
    
    pdf_files = [f for f in os.listdir(source_dir) if f.lower().endswith(".pdf")]
    if not pdf_files:
        print("No PDF files found in the source directory.")
        return

    for filename in sorted(pdf_files):
        file_path = os.path.join(source_dir, filename)
        try:
            document = process_file(file_path, client, processor_name)
            doc_dict = MessageToDict(document._pb)
            
            md_content = f"# {filename}\n\n"
            if "documentLayout" in doc_dict and "blocks" in doc_dict["documentLayout"]:
                md_content += parse_blocks(doc_dict["documentLayout"]["blocks"])
            elif "text" in doc_dict and doc_dict["text"]:
                md_content += doc_dict["text"]
            else:
                md_content += "ERROR: No layout or text content found."

            target_file = os.path.join(target_dir, filename.replace(".pdf", ".md").replace(".PDF", ".md"))
            with open(target_file, "w") as f:
                f.write(md_content)
            print(f"SUCCESS: {filename} -> markdown_google/{os.path.basename(target_file)}")
        except Exception as e:
            print(f"FAILED: {filename} - {str(e)}")

if __name__ == "__main__":
    main()
