# AI Document and File Summaries
This project's goal is to summarize a document repository of 70,000+ PDFs and Word files and 30,000 Outlook files (.msg) using Azure Cognitive Services (Document Intelligence/Form Recognizer and AI Language).

The summaries needed to be sufficient for users to quickly identify whether a document was related to a topic of research based on a word search of an index of files or visual skim through summaries, rather than having open every single document or tasking people with manually writing summaries for all the files.

## Major Categories of Code

1. FunctionApps - Azure Python V2 Function App function_app.py files
2. Notebooks - Jupyter Python notebooks used for quick testing, prototyping, and development
3. PowerShell - query and configure Azure resources
4. Terraform - deploy Azure resources

### Function Apps
All Azure Fuction Apps use the Python V2 programming model.

The first Function App (FA-pdf-sum) uses a blob storage trigger to watch a specific blob container on an Azure Data Lake Gen 2 Storage Account and execute when new files and folders are created within the path specified. If the file extension is .pdf, .doc, or .docx the file is sent to Azure Document Intelligence for OCR and text extraction. If the file is .txt then the text is extracted using standard Python methods. If the file is .msg the email metadata and body contents are extracted using the extract_msg package, and attachments are extracted and saved to blob storage for processing by the second Function App (FA-attach-summary). 

If the extracted text contains more than 1000 characters but fewer than 125,000 characters, the whole body of extracted text is sent to Azure AI Language's abstractive summary service. If the extracted text contains more than 125k characters, which is the maximum number of characters AI Language will accept, the text is broken into chunks based on the newline "\n" character closest to the 125k mark the initial and each successive section and then each chunk of text is sent to the AI Language service and the results are appended together (the end users did not want the concatenated summaries further summarized). Finally, the extracted text, abstractive summary, and other metadata is written to a record in Cosmos DB for later retrieval.

FA-attach-summary operates very similarly to FA-pdf-sum, but is set up to recurively extract text form and summarize email attachments. For example, if a .msg file originaly processed by FA-pdf-sum has an attachment that is also a .msg file, and that attached .msg file has its own attachments, then FA-attach-summary will continue processing through the nested attachments until it has processed them all.

### Notes
1. The trigger traverses down through subdirectories created in the parent folder, which was important to account for externally created directory structures synchronized from the source on-premises share drive to the Blob Storage container.
2. Using an event-based blob trigger would've been ideal, but with the storage accounts and function apps all using private endpoints with on-prem DNS, configuring event triggers requires deploying and configuring an Azure API Management instance, which was out of the scope of this particular project.