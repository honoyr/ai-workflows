import os
import json
from google.cloud import documentai_v1 as documentai
from google.api_core.client_options import ClientOptions
from google.protobuf.json_format import MessageToDict

# Configuration
PROJECT_ID = "gen-lang-client-0919716328"
LOCATION = "us"
PROCESSOR_ID = "f83a1be0c961fadd"
SOURCE_DIR = "/Users/admin/Library/CloudStorage/GoogleDrive-denis.gonor@gmail.com/My Drive/Documents/CAR/Cadilac/Cadilac_docs"
TARGET_DIR = os.path.join(SOURCE_DIR, "markdown_google")

def process_file(file_path, client, processor_name):
    print(f"Processing: {os.path.basename(file_path)}...")
    with open(file_path, "rb") as image:
        image_content = image.read()
    raw_document = documentai.RawDocument(content=image_content, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=processor_name, raw_document=raw_document)
    response = client.process_document(request=request)
    return response.document

def parse_blocks(blocks, depth=0):
    md = []
    for block in blocks:
        # Handle textBlock
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
            
            # Recurse if there are nested blocks in the textBlock
            if "blocks" in block["textBlock"]:
                md.append(parse_blocks(block["textBlock"]["blocks"], depth + 1))

        # Handle tableBlock
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
                    # Cells can contain blocks!
                    cell_text = ""
                    if "blocks" in cell:
                        cell_text = parse_blocks(cell["blocks"], depth + 1).strip().replace("\n", " ")
                    row_cells.append(cell_text)
                
                md.append("| " + " | ".join(row_cells) + " |\n")
                if i == 0 and "headerRows" in table:
                    md.append("| " + " | ".join(["---"] * len(row_cells)) + " |\n")
            md.append("\n")

        # Handle listBlock
        elif "listBlock" in block:
            for item in block["listBlock"].get("listItems", []):
                if "blocks" in item:
                    item_text = parse_blocks(item["blocks"], depth + 1).strip()
                    md.append(f"* {item_text}\n")
            md.append("\n")

    return "".join(md)

def main():
    if not os.path.exists(TARGET_DIR):
        os.makedirs(TARGET_DIR)

    opts = ClientOptions(api_endpoint=f"{LOCATION}-documentai.googleapis.com")
    client = documentai.DocumentProcessorServiceClient(client_options=opts)
    processor_name = client.processor_path(PROJECT_ID, LOCATION, PROCESSOR_ID)

    for filename in sorted(os.listdir(SOURCE_DIR)):
        if filename.endswith(".pdf"):
            file_path = os.path.join(SOURCE_DIR, filename)
            try:
                document = process_file(file_path, client, processor_name)
                doc_dict = MessageToDict(document._pb)
                
                md_content = f"# {filename}\n\n"
                
                # Check for documentLayout
                if "documentLayout" in doc_dict and "blocks" in doc_dict["documentLayout"]:
                    md_content += parse_blocks(doc_dict["documentLayout"]["blocks"])
                elif "text" in doc_dict and doc_dict["text"]:
                    md_content += doc_dict["text"]
                else:
                    md_content += "ERROR: No layout or text content found."

                target_file = os.path.join(TARGET_DIR, filename.replace(".pdf", ".md"))
                with open(target_file, "w") as f:
                    f.write(md_content)
                print(f"Successfully saved to: {target_file}")
            except Exception as e:
                print(f"Error processing {filename}: {e}")

if __name__ == "__main__":
    main()
