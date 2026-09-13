# Document Converter Skill

This skill provides a generic, path-based workflow for batch-converting PDF documents into structured Markdown files using the Google Document AI Layout Parser.

## Commands

### Convert Directory
Convert all PDFs in a specified directory and save the output to a `markdown_google/` subfolder.

```bash
python3 document_converter.py "/path/to/docs" --project "YOUR_PROJECT_ID" --processor "YOUR_PROCESSOR_ID"
```

## Setup & Requirements

1.  **GCP Processor:** You must have a **Layout Parser** processor created in Google Cloud Document AI.
2.  **Authentication:** Ensure you have authenticated your terminal:
    ```bash
    gcloud auth application-default login
    ```
3.  **Dependencies:**
    ```bash
    pip install google-cloud-documentai pandas
    ```

## Logic
The converter uses the `documentLayout` tree from Document AI to recursively build a Markdown representation, preserving:
- Hierarchical headings (`#`, `##`, etc.)
- Complex tables
- Lists and bullet points
- Reading order for multi-column documents
