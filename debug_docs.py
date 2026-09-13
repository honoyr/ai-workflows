import os
import json
from google.cloud import documentai_v1 as documentai
from google.api_core.client_options import ClientOptions
from google.protobuf.json_format import MessageToDict

# Configuration
PROJECT_ID = "gen-lang-client-0919716328"
LOCATION = "us"
PROCESSOR_ID = "f83a1be0c961fadd"
FILE_PATH = "/Users/admin/Library/CloudStorage/GoogleDrive-denis.gonor@gmail.com/My Drive/Documents/CAR/Cadilac/Cadilac_docs/2025-12-01_Lease-Agreement.pdf"

def debug_process():
    opts = ClientOptions(api_endpoint=f"{LOCATION}-documentai.googleapis.com")
    client = documentai.DocumentProcessorServiceClient(client_options=opts)
    name = client.processor_path(PROJECT_ID, LOCATION, PROCESSOR_ID)

    with open(FILE_PATH, "rb") as f:
        content = f.read()

    raw_document = documentai.RawDocument(content=content, mime_type="application/pdf")
    request = documentai.ProcessRequest(name=name, raw_document=raw_document)

    print(f"Sending request to {LOCATION} for {os.path.basename(FILE_PATH)}...")
    response = client.process_document(request=request)
    
    # Convert the protobuf message to a dictionary
    doc_dict = MessageToDict(response.document._pb)
    
    with open("debug_response.json", "w") as f:
        json.dump(doc_dict, f, indent=2)
    
    print("Full JSON response saved to debug_response.json")
    print(f"Text field length in JSON: {len(doc_dict.get('text', ''))}")
    if 'pages' in doc_dict:
        print(f"Number of pages detected: {len(doc_dict['pages'])}")

if __name__ == "__main__":
    debug_process()
