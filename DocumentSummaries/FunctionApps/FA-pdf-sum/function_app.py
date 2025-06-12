# Function App: FA-PDF-SUM
# Created by: Joshua Wilshere
# Created on: 2/27/25
# Purpose: Pass an incoming PDF or Word file from a blob storage account to the Azure AI Document
#           Intelligence service, generate an abstract summary with Azure AI Language service,
#           extract the text again using PyPDF2, extract file classification markings,
#           and write the output to a record in Cosmos DB
# 
# Developed using Python 3.11
# conda create -n "py311pip" python=3.11
# conda activate py311pip
# pip install -r requirements.txt
#
# To deploy to Azure via command line cd into the function app's directory and then run:
#
# cd <this directory>
# az login
# az account set --subscription "<target subscription name>"
# func azure functionapp publish "FA-pdf-sum"


# References: 
# https://github.com/MicrosoftDocs/azure-docs/blob/main/articles/azure-functions/functions-bindings-storage-blob-output.md
# https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-python?tabs=get-started%2Casgi%2Capplication-level&pivots=python-mode-decorators#enable-sdk-type-bindings-for-the-blob-storage-extension
# Tenacity retry: https://tenacity.readthedocs.io/en/stable/
# Logging: https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-python?tabs=get-started%2Casgi%2Capplication-level&pivots=python-mode-decorators#logging
# Log monitoring: https://learn.microsoft.com/en-us/azure/azure-functions/functions-monitoring
# KQL: https://learn.microsoft.com/en-us/training/paths/analyze-monitoring-data-with-kql/


# pylint: disable=logging-fstring-interpolation
# pylint: disable=line-too-long, trailing-whitespace, missing-function-docstring
# pylint: disable=broad-exception-raised, broad-exception-caught
# pylint: disable=too-many-arguments, too-many-positional-arguments, too-many-locals, too-many-branches, too-many-statements

import os
import re
import sys
import json
import math
import uuid
import logging
import threading
import queue
from timeit import default_timer as timer
from io import StringIO, BytesIO
from datetime import datetime as dt
import pytz
import extract_msg
import azure.functions as func
from PyPDF2 import PdfReader
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from azure.ai.textanalytics import TextAnalyticsClient
from azure.storage.blob import BlobServiceClient
from tenacity import retry, stop_after_delay, wait_exponential, before_log

AZURE_COSMOS_DATABASE_NAME = os.environ.get("AZURE_COSMOS_DATABASE_NAME")
AZURE_COSMOS_CONTAINER_NAME = os.environ.get("AZURE_COSMOS_CONTAINER_NAME") 

app = func.FunctionApp()

## Define global logger for use in @retry decorators
logger = logging.getLogger('azure')

# Blob input trigger binding
@app.blob_trigger(arg_name="blobtriggerfile",
                  path="raw/inputdocs/{subPath}/{name}",
                  connection="datalake_STORAGE") 

# Blob input binding for document classification lookup data
@app.blob_input(
                arg_name="classificationsfile",
                path="reference/doc_classifications/classifications.txt",
                connection="datalake_STORAGE")

# Cosmos DB input binding
@app.cosmos_db_input(arg_name="cosmodocsin", 
                     database_name=AZURE_COSMOS_DATABASE_NAME,
                     container_name=AZURE_COSMOS_CONTAINER_NAME,
                     sql_query="SELECT * FROM c WHERE c.filename = {name}",
                     connection="cosmosdb_CONNECTION")

# Cosmos DB output binding
@app.cosmos_db_output(arg_name="cosmodocsout", 
                      database_name=AZURE_COSMOS_DATABASE_NAME,
                      container_name=AZURE_COSMOS_CONTAINER_NAME,
                      create_if_not_exists=False,
                      connection="cosmosdb_CONNECTION")

# Initial function to verify file extension of incoming files before passing to primary function
def func_app_doc_summary_main(blobtriggerfile: func.InputStream, classificationsfile: func.InputStream, cosmodocsin: func.DocumentList, cosmodocsout: func.Out[func.Document], context: func.Context):
    file_extension = os.path.splitext(blobtriggerfile.name)[1].lower()
    dirname = os.path.dirname(blobtriggerfile.name)
    filename = os.path.basename(blobtriggerfile.name)
    allowable_extensions = ['.pdf', '.doc', '.docx', '.txt', '.msg']
    if "/Archive" in dirname:
        logging.info(f"{filename} is in Archive directory {dirname} and will be excluded. Stopping function.")
        return
    if file_extension in allowable_extensions:
        logging.info(f"{filename} is a PDF, Word Doc, text file, or Outlook email beginning file processing")
        func_app_doc_summary(blobtriggerfile, classificationsfile, cosmodocsin, cosmodocsout, context, filename, dirname, file_extension)
    else:
        logging.info(f"{filename} is not a PDF, Word Doc, text file, or Outlook email skipping file processing")
        return


# Primary function
def func_app_doc_summary(blobtriggerfile: func.InputStream, classificationsfile: func.InputStream, cosmodocsin: func.DocumentList, cosmodocsout: func.Out[func.Document], context: func.Context, filename, dirname, file_extension):
    # Set timezone to East Coast for easily readable timestamps within logging messages (doesn't affect the log's automatic timestamps)
    tz = pytz.timezone("America/New_York")
    # Acquire start time of function
    start_time = dt.now(tz).strftime('%Y-%m-%d %H:%M:%S %f')
    # Start timer
    start = timer()

    # Generate Unique ID
    unique_id = str(uuid.uuid4())

    # Create logger with Azure context
    logger = logging.getLogger('azure')
    # Set Log Level - if more troubleshooting needed, set to logging.DEBUG, otherwise set to logging.INFO
    logger.setLevel(logging.INFO)

    ## Uncomment below lines to output log info to terminal (useful when developing and testing locally)
    # stdout_handler = logging.StreamHandler(stream=sys.stdout)
    # logger.addHandler(stdout_handler)

    # Write initial info to log
    logging.info(f"Python blob trigger function processed blob"
            f"Name: {blobtriggerfile.name} \n"
            f"Unique ID: {unique_id} \n"
            f"Function Start Time: {start_time} \n"
            f"Function Invocation ID: {context.invocation_id}\n")
    
    record_count = len(cosmodocsin)

    logging.info(f"Cosmos DB query returned {record_count} results for {filename}\n")

    try:
        # Read the blob file into a bytes object
        blob_data = blobtriggerfile.read()
        
        # Initialize variables - defaults for files without an existing Cosmos DB record
        text_missing_flag = True
        summary_missing_flag = True
        filemarkings_missing_flag = True
        record_exists = False
        record_version = 1

        # Set the default based on whether the incoming file is a pdf or not
        pypdf2_missing_flag = file_extension.lower() == '.pdf'

        # # Get the connection strings and other secrets from environment variables in local.settings.json
        form_recognizer_key = os.getenv('FORM_RECOGNIZER_KEY')
        form_recognizer_endpoint = os.getenv('FORM_RECOGNIZER_ENDPOINT')
        ai_language_key = os.getenv('AI_LANGUAGE_KEY')
        ai_language_endpoint = os.getenv('AI_LANGUAGE_ENDPOINT')

        # Check for existance of Cosmos DB record for incoming file name
        if record_count <= 0:
            logging.info(f"Processing {filename} as new file.\n")

        # When record exists, check to see if it contains extracted text and summary for file
        elif record_count == 1:
            #  Update variables accordingly
            record_exists = True
            for item in cosmodocsin:
                dict_output, text_missing_flag, summary_missing_flag, pypdf2_missing_flag, filemarkings_missing_flag = record_contents_check(item, filename)
                # Get version of existing record
                record_version = dict_output["record_version"]
                logging.info(f"{record_count} record found for file '{filename}'. Record version is {record_version}. Processing...\n")
                
                # If the "error" property exists in the record returned from Cosmos DB and it has error messages from a previous run
                #   And a new text extract, new summary, or both needs to be generated
                #   Then archive those error messages to a new dict based on the record version and then delete the base error entry
                if "error" in dict_output and (text_missing_flag or summary_missing_flag):
                    if len(dict_output["error"]) > 0:
                        error_archive_property = f"error_archive_v{record_version}"
                        dict_output[error_archive_property] = dict_output["error"]
                        del dict_output["error"]
                        logging.info(f"Filename: {filename} Record contains error from previous run in version {record_version}. Archiving old error message to '{error_archive_property}' and resetting 'error' property\n")
                
                # Increment the record version
                dict_output["record_version"] = record_version + 1
                # Update the invocation id with the current one
                dict_output["current_version_invocation_id"] = context.invocation_id


        # If more than 1 record is returned for the filename sent in the query, write error to log and exit the function
        else:
            logging.error(f"ERROR: Too many records returned. {record_count} records exist for file {filename}. Each file should only have a single record.\n")
            return
        
        # If the record already exists and contains extracted text and an abstractive summary, return to exit the function
        if record_exists and not text_missing_flag and not summary_missing_flag and not filemarkings_missing_flag and not pypdf2_missing_flag:
            logging.info(f"Record already exists for {filename} and contains extracted text, abstractive summary, file markings, and the pypdf2 text extract. Exiting function.\n")
            return
        
        # Initialize the output structure if there is not existing record for the file
        if not record_exists:
            dict_output = {
                "id": unique_id,
                "filename": filename,
                "filepath": dirname,
                "filetype": file_extension,
                "abstractsummary": '',
                "fulltextextract": '',
                "filemarkings": '',
                #"first_last_lines": '',
                "textextract_metadata": {
                    "fulltextextract_length": '',
                    "fulltextextract_status": '',
                    "document_pages": ''},
                "summarization_metadata":{
                },
                "timestamps": {
                    "function_start": '',
                    "extraction_start": '',
                    "extraction_finish": '',
                    "extraction_duration": ''
                },
                "pypdf2_text_extract": '',
                "record_version": record_version,
                "current_version_invocation_id": context.invocation_id
            }

            # Set the function start time for new and existing/updating records
            dict_output["timestamps"]["function_start"] = start_time

    #################################
    #### TEXT EXTRACTION SECTION ####
    #################################

        # If the function needs to extract text from the file
        if text_missing_flag:
            
            # Get timestamp before text extraction starts
            txt_extract_start_timestamp = dt.now(tz).strftime('%Y-%m-%d %H:%M:%S %f')

            logging.info(f"Filename: {filename} Text extraction starting at: {txt_extract_start_timestamp}\n")

            # Initialize Document Analysis Client
            document_analysis_client = DocumentAnalysisClient(
                endpoint=form_recognizer_endpoint,
                credential=AzureKeyCredential(form_recognizer_key)
            )

            # Acquire the extracted text and metadata
            #### PDF/DOC(X) ####
            if file_extension in ['.pdf', '.doc', '.docx']:
                txt_result_status, txt_result_length, page_count, txt_result, pypdf2_text_dict = text_extraction(blob_data, document_analysis_client, pypdf2_missing_flag)
                dict_output["textextract_metadata"]["document_pages"] = page_count
                if pypdf2_missing_flag:
                    dict_output['pypdf2_text_extract'] = pypdf2_text_dict
            #### TXT ####
            elif file_extension == '.txt':
                txt_result = blob_data.decode('utf-8')
                txt_result_length = len(txt_result)
                if txt_result_length > 0:
                    txt_result_status = 'succeeded'
                else:
                    txt_result_status = 'failed'
                del dict_output["textextract_metadata"]["document_pages"]
            #### MSG ####
            elif file_extension == '.msg':
                dict_output['email_properties']={}
                txt_result, email_properties = email_extraction(blob_data, filename)
                dict_output['email_properties']=email_properties
                txt_result_length = len(txt_result)
                if txt_result_length > 0:
                    txt_result_status = 'succeeded'
                else:
                    txt_result_status = 'failed'
            #### SOMETHING ELSE SLIPPED THROUGH ####
            else:
                logging.error(f"Filename: {filename} - unable to process file of type {file_extension}")
                return
            
            if file_extension != '.pdf':
                del dict_output['pypdf2_text_extract']

            # logging.info(f"Filename: {filename} Retry statistics for text extraction:\n{text_extraction.retry.statistics}\n")

            # Get timestamp when results are obtained
            txt_extract_end_timestamp = dt.now(tz).strftime('%Y-%m-%d %H:%M:%S %f')
            extract_end = timer() 

            logging.info(f"Filename: {filename} Text extraction completed at {txt_extract_end_timestamp} with status: {txt_result_status}\nText Length: {txt_result_length}\n")

            # Update json output structure for the new or existing record
            dict_output["fulltextextract"] = txt_result
            dict_output["textextract_metadata"]["fulltextextract_length"] = txt_result_length
            dict_output["textextract_metadata"]["fulltextextract_status"] = txt_result_status
            extract_end = timer()
            dict_output["timestamps"]["function_start"] = txt_extract_end_timestamp
            dict_output["timestamps"]["extraction_start"] = txt_extract_start_timestamp
            dict_output["timestamps"]["extraction_finish"] = txt_extract_end_timestamp
            dict_output["timestamps"]["extraction_duration"] = str(extract_end-start)

        # If the document intelligence text extract exists but the pypdf2 extract is missing and should exist,
        #   run the pypdf2 text extract function and read in the existing fulltextextract and related metadata
        elif not text_missing_flag and pypdf2_missing_flag:
            logging.info(f"Filename: {filename} Extracting text from PDF using PyPDF2 package")
            pypdf2_text_dict = pypdf2_text_extraction(blob_data)
            dict_output['pypdf2_text_extract'] = pypdf2_text_dict
            txt_result = dict_output['fulltextextract']
            txt_result_length = dict_output["textextract_metadata"]["fulltextextract_length"]
            txt_result_status = dict_output["textextract_metadata"]["fulltextextract_status"]

        # Else, get the existing text extract and text length from the Cosmos DB record
        else:
            logging.info(f"Filename: {filename} Reading in text extract from existing record")
            txt_result = dict_output['fulltextextract']
            txt_result_length = dict_output["textextract_metadata"]["fulltextextract_length"]
            txt_result_status = dict_output["textextract_metadata"]["fulltextextract_status"]
            if file_extension == '.pdf':
                logging.info(f"Filename: {filename} Reading in pypdf2 extract from existing record")
                pypdf2_text_dict = dict_output['pypdf2_text_extract']

    ##################################################
    #### DOCUMENT CLASSIFICATION/MARKINGS SECTION ####
    ##################################################

        # If the function needs to generate the document classification markings based on the extracted text
        if filemarkings_missing_flag:
            # Read in the classifications reference/lookup file to a list
            #   By default the InputStream format reads in data as "bytes" type, so they must be decoded for downstream string operations
            classifications = [x.decode('utf8').strip() for x in classificationsfile.readlines()]
            # Reverse sort the list based on length so that the longer, more complete classification strings are matched first
            classifications.sort(key=len, reverse=True)
            # Attempt to generate file markings based on the text extracted by Azure Document Intelligence
            filemarkings = extract_classification(txt_result, classifications)
            # If no file markings are found in the Azure Document Intelligence extract, attempt to find a match in the pypdf2 text extract
            if file_extension == '.pdf':
                if len(filemarkings) <= 0 < len(dict_output['pypdf2_text_extract']['pypdf2_fulltext']):
                    filemarkings = extract_classification(dict_output['pypdf2_text_extract']['pypdf2_fulltext'], classifications)
            # If file markings are matched, update the filemarkings attribute with the dict returned by the extract_classification function
            if len(filemarkings) > 0:
                dict_output['filemarkings'] = filemarkings

    #########################
    #### SUMMARY SECTION ####
    #########################
        
        # If the function needs to generate a summary of the extracted text
        # Only proceed to text summarization if the text extraction succeeded and there is extracted text to summarize
        if summary_missing_flag and txt_result_status == 'succeeded' and len(txt_result.strip()) > 0:
            
            # Initialize starting time stamps and timers for summary task
            summary_start_timestamp = dt.now(tz).strftime('%Y-%m-%d %H:%M:%S %f')
            summ_start = timer()

            logging.info(f"Filename: {filename} Beginning text summarization at: {summary_start_timestamp}\n")

            # Initialize AI Language Client
            text_analytics_client = TextAnalyticsClient(
            endpoint=ai_language_endpoint, 
            credential=AzureKeyCredential(ai_language_key))
     
            #############################
            ##### SHORT TEXT SECTION ####
            #############################

            # If the full text extract is less than or equal to 125,000 characters (Azure AI Langugage service limit per submission)
            if txt_result_length <= 125000:
                # Add extract text to list to feed to AI Language Summary service
                document = [txt_result]

                # # Send extracted to AI Language Abstractive Summary Service
                abstractive_summary_result = abstract_summary(text_analytics_client, document)

                # Capture timestamps and durations of the summary call
                summary_finish_timestamp = dt.now(tz).strftime('%Y-%m-%d %H:%M:%S %f')
                summ_end = timer()

                logging.info(f"Filename: {filename} Retry statistics for summary:\n{abstract_summary.retry.statistics}\n")

                # Handle any errors
                if abstractive_summary_result.is_error:
                    logging.error(f"Document summarization encountered an error with code '{abstractive_summary_result.code}' and message '{abstractive_summary_result.message}'\n")
                    dict_output["summarization_metadata"]["summary_error_code"] = abstractive_summary_result.code
                    dict_output["summarization_metadata"]["summary_error_message"] = abstractive_summary_result.message
                # If no errors, append the abstractive summary and metdata to the json output
                else:
                    # Combines/joins all text from the ItemPaged list object together into a single string
                    abstr_summary = concat_text_chunks([summary.text for summary in abstractive_summary_result.summaries])
                    dict_output["abstractsummary"] = abstr_summary

                    input_length = [summary.contexts[0].length for summary in abstractive_summary_result.summaries][0]
                    dict_output["summarization_metadata"]["text_input_length"] = input_length
                    dict_output["summarization_metadata"]["summary_length"] = len(abstr_summary)
                    dict_output["timestamps"]["summary_start"]=summary_start_timestamp
                    dict_output["timestamps"]["summary_finish"]=summary_finish_timestamp
                    dict_output["timestamps"]["summary_duration"]=str(summ_end-summ_start)
                    end1 = timer()
                    dict_output["timestamps"]["total_duration"]=str(end1-start)

                    logging.info(f"Filename: {filename} Document summary complete at: {summary_finish_timestamp}")
            
                    
            ###########################
            #### LONG TEXT SECTION ####
            ###########################

            # Break up and process the extracted texts into chunks with 125,000 or fewer characters
            # Submit each chunk to the summarization service and then append the results together
            else:
                
                logging.info(f"Filename: {filename} Extracted text is longer than 125,000 characters breaking into smaller chunks to summarize.")
                
                final_summary = ""
                # Initialize the required nested dictionary keys if they don't exist
                if 'abstractsummary_parts' not in dict_output:
                    dict_output['abstractsummary_parts'] = {}
                if 'timestamps' not in dict_output:
                    dict_output['timestamps'] = {}
                
                # Initialize total length variables
                total_summary_text_input_length = 0
                total_summary_length = 0

                # Loop over the text and break it up into chunks of 125,000 characters or less
                #   End the chunk on the newline character (\n) closest to the 125,000 character count
                for x in range(math.ceil(len(txt_result)/125000)):
                    # Set the summary part number for use in dict keys (looks like "summarypart00", "summarypart01", "summarypart19", etc)
                    summary_part = f"summarypart{str(x).zfill(2)}"

                    summary_part_start_timestamp = dt.now(tz).strftime('%Y-%m-%d %H:%M:%S %f')

                    logging.info(f"Filename: {filename} {summary_part} starting at {summary_part_start_timestamp}")

                    # Initialize the required nested dictionary keys for each summary part
                    #   dict_output['abstractsummary_parts']['summarypart00']
                    if summary_part not in dict_output['abstractsummary_parts']:
                        dict_output['abstractsummary_parts'][summary_part] = {}
                    #   dict_output['summarization_metadata']['summarypart00']
                    if summary_part not in dict_output['summarization_metadata']:
                        dict_output['summarization_metadata'][summary_part] = {}
                    #   dict_output['timestamps']['summarypart00']
                    if summary_part not in dict_output['timestamps']:
                        dict_output['timestamps'][summary_part] = {}

                    # Start the timer for the summary of this chunk of text
                    summary_part_timer = timer()

                    # Empty and re-initialize the list used to submit text to the Language service
                    document = []

                    # Find the position of the \n character closest to the end of a substring of the first 125k characters
                    newline_position = txt_result[0:125000].rfind('\n')
                    # Add the text between the start and the position returned above to the list
                    document.append(txt_result[0:newline_position])
                    # Remove the appended text from the body of the whole text
                    txt_result = txt_result[newline_position:]

                    # Send the text to the abstractive summary service
                    abstractive_summary_result = abstract_summary(text_analytics_client, document)

                    # Capture timestamps and durations of the summary call
                    summary_finish_timestamp = dt.now(tz).strftime('%Y-%m-%d %H:%M:%S %f')
                    summ_end = timer()

                    #logging.info(f"Filename: {filename} Retry statistics for {summary_part}:\n{abstract_summary.retry.statistics}\n")


                    if abstractive_summary_result.is_error:
                        logging.error(f"There is an error summarizing file {filename} with code '{abstractive_summary_result.code}' and message '{abstractive_summary_result.message}'")
                        # Initialize the error dictionary key if it doesn't already exist
                        if 'error' not in dict_output:
                            dict_output["error"] = {}
                        dict_output["error"][f"{summary_part}_error_code"] = abstractive_summary_result.code
                        dict_output["error"][f"{summary_part}_error_message"] = abstractive_summary_result.message
                    # If no errors, append the abstractive summary for the chunk of text and associated metdata to the json/dict output
                    else:
                        # Combines/joins all text from the ItemPaged list object together into a single string 
                        #abstr_summary = "".join([summary.text for summary in abstractive_summary_result.summaries])
                        abstr_summary = concat_text_chunks([summary.text for summary in abstractive_summary_result.summaries])                          
                        dict_output["abstractsummary_parts"][summary_part] = abstr_summary
                        # Get the metadata from the result
                        input_length = [summary.contexts[0].length for summary in abstractive_summary_result.summaries][0]
                        total_summary_text_input_length += input_length
                        summary_length = len(abstr_summary)
                        total_summary_length += summary_length
                        dict_output["summarization_metadata"][summary_part]["text_input_length"] = input_length
                        dict_output["summarization_metadata"][summary_part]["summary_length"] = summary_length
                        dict_output["timestamps"][summary_part][f"{summary_part}_start"]=summary_part_start_timestamp
                        dict_output["timestamps"][summary_part][f"{summary_part}_finish"]=summary_finish_timestamp
                        dict_output["timestamps"][summary_part][f"{summary_part}_duration"]=str(summ_end-summary_part_timer)

                        # Append the summary of the chunk to the summaries of previous chunks
                        final_summary = f"{final_summary} {abstr_summary}"

                        logging.info(f"Document {filename} {summary_part} complete at: {summary_finish_timestamp}")

                # Capture the end time of the whole summarization process    
                end_time = timer()

                # Ensure the final appended summary is not blank
                #   And then write the final summary and metadata to the json/dict output
                if len(final_summary.strip()) > 0:
                    dict_output["abstractsummary"] = final_summary
                    dict_output["summarization_metadata"]["text_input_length"] = total_summary_text_input_length
                    dict_output["summarization_metadata"]["summary_length"] = total_summary_length
                    dict_output["timestamps"]["summary_start"]=summary_start_timestamp
                    dict_output["timestamps"]["summary_finish"] = summary_finish_timestamp
                    dict_output["timestamps"]["summary_duration"]=str(summ_end-summ_start)
                    dict_output["timestamps"]["total_duration"] = str(end_time-start)

                    logging.info(f"Filename: {filename} Document complete at: {summary_finish_timestamp}")

                # If the final summary is blank, write an error message to the log and to the json/dict output
                else:
                    # Initialize the error dictionary key if it doesn't already exist
                    if 'error' not in dict_output:
                        dict_output["error"] = {}
                    dict_output["error"]["final_summary_error"] = 'Something went wrong creating summary of long document'
                    logging.error(f'Something went wrong creating summary of long document for {filename}')

        # Else, If the text extraction succeeds but the document does not have any extractable text, write that to the record and the log
        elif txt_result_status == 'succeeded' and len(txt_result.strip()) == 0:
            dict_output["fulltextextract"] = 'No extractable text found'
            
            logging.error(f'No extractable text found in file {filename}, skipping summarization step - skip test 1')

        # Else, if the text extraction does not succeed, check to see if any extracted text is returned, and then write an error in the record and to the log
        else:
            if 'error' not in dict_output:
                dict_output['error'] = {}
            if len(txt_result.strip()) > 0:
                dict_output['error']['text_extraction_status'] = txt_result_status
                logging.error(f'No extractable text found in file {filename}, skipping summarization step - skip test 2')
            else:
                dict_output["fulltextextract"] = 'No extractable text found'
                dict_output['error']['text_extraction_status'] = txt_result_status
                logging.error(f'No extractable text found in file {filename}, skipping summarization step - skip test 3')
        
        # Convert/serialize the JSON/dict object to a string for output
        json_output=json.dumps(dict_output)
        
        if sys.getsizeof(json_output) > 2097152:
            overage_size = sys.getsizeof(json_output) - 2097152
            logging.warning(f"json output is {sys.getsizeof(json_output)} bytes, which is {overage_size} bytes over the limit")
            # Output json contents to Blob Storage as json file with same base name as the input file
            #outputblob.set(json_output)
            if 'pypdf2_text_extract' in dict_output:
                if 'pypdf2_fulltext_by_page' in dict_output['pypdf2_text_extract']:
                    logging.warning('Deleting pypdf2_fulltext_by_page from dict_output to reduce size')
                    del dict_output['pypdf2_text_extract']['pypdf2_fulltext_by_page']
                # Convert the dictionary back to json after dropping 'pypdf2_fulltext_by_page'
                json_output=json.dumps(dict_output)
                # If the size of the dict is still greater than 2 MB, drop the whole pypdf2_text_extract property from the dict
                if sys.getsizeof(json_output) > 2097152:
                    logging.warning('Deleting pypdf2_text_extract from dict_output to reduce size')
                    del dict_output['pypdf2_text_extract']
                    # Convert the dictionary back to json after dropping 
                    json_output=json.dumps(dict_output)

        #logging.info(json_output)

        # Output json contents to Blob Storage as json file with same base name as the input file
        #outputblob.set(json_output)

        # Output json contents to Cosmos DB instance
        #cosmodocsout.set(func.Document.from_json(json_output)) 
        try:    
            cosmodocsout.set(func.Document.from_json(json_output)) 
        except Exception as inner_e:
            logging.error(f"Filename: {filename} An error occurred loading the json_output to the cosmos_db, the size of the json_output is {sys.getsizeof(json_output)}.\n\
                           Deleting dict_output so main error writes a default one to cosmos db to capture error. Error message: \n{inner_e}", exc_info=True)
            del dict_output
            raise
        
        end2 = timer()
        logging.info(f"Processing of {filename} complete in {str(end2-start)} seconds. Unique ID is {unique_id}")



    except Exception as e:
        logging.error(f"An error occurred while processing {filename} {unique_id}: {e}", exc_info=True)
        if 'dict_output' not in locals():
            dict_output = {
                "id": unique_id,
                "filename": filename,
                "filepath": dirname,
                "filetype": file_extension,
                "textextract_metadata": {},
                "summarization_metadata":{},
                "timestamps": {
                    "function_start": start_time},
                "current_version_invocation_id": context.invocation_id}
        if 'error' not in dict_output:
            dict_output['error'] = {} 
        dict_output["error"]["exception"] = str(e)
        json_output=json.dumps(dict_output)
        #outputblob.set(json_output)
        cosmodocsout.set(func.Document.from_json(json_output))

        # if 'txt_result_status' in locals():
        #     logging.error(f"Retry statistics for text extraction of {filename}:\n{text_extraction.retry.statistics}\n")

        # if 'abstractive_summary_result' in locals():
        #     logging.error(f"Retry statistics for summary of {filename}:\n{abstract_summary.retry.statistics}\n")

# Function to check if existing record in Cosmos DB for the file
#  has all the necessary elements (extracted text and summary)
def record_contents_check(item, filename):
    file_extension = os.path.splitext(filename)[1]
    # Convert Cosmos DB record to dict
    dict_output = func.Document.to_dict(item)
    #logging.info(f"\n\nCosmos Item returned: \n{dict_output}\n\n")
    text_missing_flag = False
    summary_missing_flag = False
    pypdf2_missing_flag = False
    filemarkings_missing_flag = False
    # Check if the fulltextextract attribute is missing or empty
    if 'fulltextextract' in item:
        if len(item['fulltextextract']) <=0:
            logging.info(f"Text extract is blank for {filename}, extract text\n")
            text_missing_flag = True
            # Determine whether the filemarkings attribute is missing or empty
            if 'filemarkings' in item:
                if len(item['filemarkings']) <=0:
                    logging.info(f"File markings/classifications is blank for {filename}, rerun classification analysis\n")
                    filemarkings_missing_flag = True
                else:
                    logging.info(f"File markings/classifications exists for {filename}\n")
            else:
                logging.info(f"No file markings key found for {filename}\n")
                filemarkings_missing_flag = True 
            #dict_output['fulltextextract'] = 'updated full text extract'
        else:
            logging.info(f"Text extract exists for {filename}\n")
    else:
        logging.info(f"No text extract key found for {filename}\n")
        #dict_output['fulltextextract'] = ''
        text_missing_flag = True
    # Check to see if the abstractsummary attribute is missing or empty
    if 'abstractsummary' in item:
        if len(item['abstractsummary']) <= 0:
            logging.info(f"No summary found for {filename}\n")
            #dict_output['abstractsummary'] = 'updated abstract summary'
            summary_missing_flag = True
        else:
            logging.info(f"Summary exists for {filename}\n")
    else:
        logging.info(f"No abstractsummary key found for {filename}\n")
        summary_missing_flag = True
        #dict_output['abstractsummary'] = ''
    # If the file is a pdf, check to see if it's missing the pypdf2_text_extract contents or record attribute
    if file_extension == '.pdf':
        if 'pypdf2_text_extract' in item:
            if 'pypdf2_fulltext' in item['pypdf2_text_extract']:
                if len(item['pypdf2_text_extract']['pypdf2_fulltext']) <= 0:
                    logging.info(f"No pypdf2_text_extract found for {filename}\n")
                    pypdf2_missing_flag = True
                else:
                    logging.info(f"pypdf2_text_extract exists for {filename}\n")
            else:
                logging.info(f"No pypdf2_text_extract[pypdf2_fulltext] attribute exists for {filename}\n")
                pypdf2_missing_flag = True
        else:
            logging.info(f"No pypdf2_text_extract attribute found for {filename}\n")
            pypdf2_missing_flag = True
    
    return dict_output, text_missing_flag, summary_missing_flag, pypdf2_missing_flag, filemarkings_missing_flag


# Retry with exponential backoff, with a cap of 5 minutes
@retry(reraise=True, stop=stop_after_delay(300),wait=wait_exponential(multiplier=1, min=20, max=30), before=before_log(logger, logging.INFO))
# Send file to text extraction service and get a result
def text_extraction(blob_data, document_analysis_client, pypdf2_missing_flag):

    bytes_content = BytesIO(blob_data)
    # Send the PDF to Azure AI Document Intelligence to extract text
    # Using "prebuild-read" model instead of "prebuild-layout" because of more reliably formatted output
    # (It does a better job of combining lines into the right sentances and paragraphs)
    di_poller = document_analysis_client.begin_analyze_document(
        model_id="prebuilt-read", document=bytes_content
    )
    
    # Get the results - will automatically wait/retry until results are available from service
    di_result = di_poller.result()

    # Get the status of the request results
    txt_result_status = di_poller.status()

    # Obtain the results content
    txt_result = di_result.content

    # Get the character length of the extracted text and the page length of the original document
    txt_result_length = len(txt_result)
    page_count = len(di_result.pages)

    #first_last_lines = agg_first_last_line(di_result)

    if pypdf2_missing_flag:
        pypdf2_text_dict = pypdf2_text_extraction(blob_data)
    else:
        pypdf2_text_dict = ''

    return txt_result_status, txt_result_length, page_count, txt_result, pypdf2_text_dict


# Retry with exponential backoff, with a cap of 5 minutes
@retry(reraise=True, stop=stop_after_delay(300),wait=wait_exponential(multiplier=1, min=30, max=45), before=before_log(logger, logging.INFO))
# Send text to abstractive summary service and get a result
def abstract_summary(text_analytics_client, document):

    # Send the text to the abstractive summary service
    sum_poller = text_analytics_client.begin_abstract_summary(document)

    # Get the result as an ItemPaged iterator object
    #   This includes a built-in wait and retry function
    document_results = sum_poller.result()

    # Consume the document_results iterator to a list 
    # If this isn't done, once the output is expanded and iterated over, it's flushed from memory
    summary_results_list = list(document_results)
    abstractive_summary_result = summary_results_list[0]  # first document, first result (only result since only 1 document was sent to AI Language Abstract Summarization service)

    return abstractive_summary_result

# Function to concatenate summaries together when text input is >= 125,000 characters
def concat_text_chunks(chunks):
    buffer = StringIO()
    for chunk in chunks:
        buffer.write(chunk)
    return buffer.getvalue()

# Function to match and extract document classification from text 
# Return the classification and the full line of containing text, if matched, otherwise returns blank dict
def extract_classification(text, classifications):
    try:
        filemarkings = {
            "classification": '',
            "full_document_classification_line": ''
        }
        for classification in classifications:
            # Allow classifications to be found anywhere in the text
            if "/" in classification:  # Adjust regex for classifications with slashes
                pattern = re.escape(classification)
            else:
                pattern = r'\b' + re.escape(classification) + r'\b'
            
            # search each line in the text for the classifications
            # once the text matches on a classification, return the classification and the full line of containing text
            for line in text.splitlines():
                if re.search(pattern, line, re.IGNORECASE):
                    filemarkings["classification"] = classification
                    filemarkings["full_document_classification_line"] = line
                    #return classification, line
                    return filemarkings
        # if none of the classifications match in the text, return blank values
        return ''
    except Exception as e:
        logging.error(f"An error occurred in function extract_classification: {e}", exc_info=True)
        raise Exception("Problem in function extract_classification") from e

# Function to extract the first and last line from each page of the extracted text
#   and then identify the most common first line and last line
# The purpose is to help identify and validate document classification markings in post-processing/analysis
#   since markings are supposed to be at the top and/or bottom of each page in the marked file
# Returns a dictionary of the results
# def agg_first_last_line(di_result):
#     try:
#         first_last_lines = {}
#         first_lines = []
#         last_lines = []

#         for page in di_result.pages:
#             lines = page.lines
#             if lines:
#                 first_lines.append(lines[0].content)
#                 last_lines.append(lines[-1].content)
        
#         if len(first_lines) > 0:
#             first_last_lines['firstLines'] = first_lines
#             fl_count_list = Counter(first_lines)
#             first_last_lines['most_common_first_line'] = fl_count_list.most_common(1)[0][0]
#             first_last_lines['most_common_first_line_count'] = fl_count_list.most_common(1)[0][1]

#         if len(last_lines) > 0:
#             first_last_lines['lastLines'] = last_lines
#             ll_count_list = Counter(last_lines) 
#             first_last_lines['most_common_last_line'] = ll_count_list.most_common(1)[0][0]
#             first_last_lines['most_common_last_line_count'] = ll_count_list.most_common(1)[0][1]
        
#         return first_last_lines
#     except Exception as e:
#         logging.error(f"An error occurred in function agg_first_last_line: {e}", exc_info=True)
#         raise Exception("Problem in function agg_first_last_line") from e

# Function to extract the text from PDFs using PyPDF2 Python Package
# Intended use of this function is for extracting text from SMS records converted to PDF
# Based on testing extracting the text via PyPDF2 retains the structure of the text
#     better than extraction using Azure Document Intelligence prebuilt-read model
def pypdf2_text_extraction(blob_data, timeout=60):
    """
    Extract text from a PDF file with a timeout mechanism.
    
    Args:
        pdf_path (str): Path to the PDF file
        timeout (int): Maximum time to wait for text extraction in seconds
    
    Returns:
        str: Extracted text or error message
    """
    # Queue to store extraction result
    full_text_queue = queue.Queue()
    
    # Exception queue to capture any errors during extraction
    exception_queue = queue.Queue()

    pypdf2_text_dict = {
        "pypdf2_page_count": '',
        "pypdf2_fulltext": '',
        "pypdf2_fulltext_by_page": {}
    }
    
    def pdf_extraction_worker():
        try:
            # Open the PDF file
            # stream_content = blobtriggerfile.read()
            bytes_content = BytesIO(blob_data)
            pdf_reader = PdfReader(bytes_content)
            # Open the PDF file
               
            # Extract text from all pages
            full_text = ""
            #for page in pdf_reader.pages:
            for page in enumerate(pdf_reader.pages):
                #logging.info(f'\nPage: {page_num}\n')
                page_obj = pdf_reader.pages[page[0]]
                page_text = page_obj.extract_text()
                full_text += page_text + "\n"
                pypdf2_text_dict['pypdf2_fulltext_by_page'][f'page_number_{page[0]}'] = page_text
            pypdf2_text_dict['pypdf2_page_count'] = len(pdf_reader.pages)
            
            # Put the extracted text in the queue
            full_text_queue.put(full_text.strip())
        
        except Exception as e:
            # Capture any exceptions that occur during extraction
            exception_queue.put(e)
            logging.error(f"An error occurred in function 'pdf_extraction_worker': {e}", exc_info=True)
            raise Exception("Problem in function pdf_extraction_worker") from e
    
    try:
        # Create and start the extraction thread
        extraction_thread = threading.Thread(target=pdf_extraction_worker)
        extraction_thread.start()
        
        # Wait for the thread to complete or timeout
        extraction_thread.join(timeout)
        
        if not full_text_queue.empty():
            pypdf2_text_dict['pypdf2_fulltext'] = full_text_queue.get()
        else:
            pypdf2_text_dict['pypdf2_fulltext'] = 'No text extracted'

        # Check if the thread is still alive (timed out)
        if extraction_thread.is_alive():
            pypdf2_text_dict['pypdf2_timeout_error'] = f"Error: Text extraction timed out after {timeout} seconds"
            logging.error(f"Error: PyPDF2 text extraction timed out after {timeout} seconds")
            return pypdf2_text_dict
        
        # Check if an exception occurred
        if not exception_queue.empty():
            error = exception_queue.get()
            pypdf2_text_dict['pypdf2_error'] = f"Error during PyPDF2 PDF extraction: {str(error)}"
            logging.error(f"Error during PyPDF2 PDF extraction: {str(error)}")
            return pypdf2_text_dict

    except Exception as e:
        logging.error(f"An error occurred in function pypdf2_text_extraction: {e}", exc_info=True)
        raise Exception("Problem in function pypdf2_text_extraction") from e
    
    # Retrieve and return the extracted text
    return pypdf2_text_dict

# Function to extract email content, email metadata, and email attachments
# Email attachments are saved back to blob storage to be processed by the email attachment function app
def email_extraction(blob_data, filename):

    # Initialize return values
    email_properties = {}
    email_body = ''
    
    try:
        # Read the .msg into an extract_msg object
        msg = extract_msg.openMsg(blob_data)
        
        # Pull out the desired values from the email contents and metadata
        email_body = msg.body
        email_sender = msg.sender
        email_date = msg.date.strftime('%Y-%m-%d %H:%M:%S')
        email_subject = msg.subject
        recip_names = []
        recip_emails = []
        for msg_recip in msg.recipients:
            recip_names.append(msg_recip.name)
            recip_emails.append(msg_recip.email)
        email_to = msg.to
        email_cc = msg.cc
        email_bcc = msg.bcc
        
        # Updat the blank dictionary with the values
        email_properties['sender'] = email_sender
        email_properties['email_date'] = email_date
        email_properties['email_subject'] = email_subject
        email_properties['to'] = email_to
        if email_cc:
            email_properties['cc'] = email_cc
        if email_bcc:
            email_properties['bcc'] = email_bcc
        email_properties['recipient_names'] = recip_names
        email_properties['recip_emails'] = recip_emails
        
        # IF Statement to check whether attachments exist before running code to save them to blob storage
        if msg.attachments:

            # Create blob container client to save attachments
            storage_connection_string = os.getenv("datalake_STORAGE")
            storage_container_name = os.getenv('STORAGE_CONTAINER_NAME')
            blob_service_client = BlobServiceClient.from_connection_string(storage_connection_string)
            container_client = blob_service_client.get_container_client(storage_container_name)
            # Get the name of the email file without the extension to use as the top level folder name the attachments will be saved into            
            base_filename = os.path.splitext(filename)[0]

            # Initialize lists to record attachment file names and blob paths
            attachment_list = []
            attachment_list_full_path = []
            
            # Save email attachments to blob storage
            for attachment in msg.attachments:
                if attachment.longFilename:
                    logging.info(f"Attachment filename: {attachment.longFilename}")

                    attachment_name = f"email_attachments/{base_filename}/{attachment.displayName}"
                    attachment_content = attachment.data
                    
                    attachment_list.append(attachment.displayName)
                    attachment_list_full_path.append(attachment_name)
                    # Upload attachment to Blob Storage
                    blob_client = container_client.get_blob_client(attachment_name)
                    blob_client.upload_blob(attachment_content, overwrite=True)
                    
                    logging.info(f"Uploaded attachment: {attachment_name}")

                    logging.info("Finished processing .msg file and uploading attachments")

                else:
                    logging.info("Skipping attachment with None filename.")

            
            email_properties['attachment_filenames'] = attachment_list
            email_properties['attachment_blob_names'] = attachment_list_full_path
    
    except Exception as e:
        logging.error(f"An error occurred in function email_extraction: {e}", exc_info=True)
        raise Exception("Problem in function email_extraction") from e
    
    return email_body, email_properties
