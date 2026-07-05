import requests
from pathlib import Path
import re
from markdownify import markdownify as md
import os
from dotenv import load_dotenv
import glob

from google import genai
from google.genai import errors, types
import time

import hashlib

CHANGED_FILES = set()
RUN_COUNTS = {
    "added": 0,
    "updated": 0,
    "skipped": 0,
    "uploaded": 0,
}


# Make the slug of the output file
def slugify(value):
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


# Fetch Articles
def fetchArticlesUrl(size: int) -> list[str]:
    url = f"https://support.optisigns.com/api/v2/help_center/articles?page[size]={size}"
    res = requests.get(url, timeout=30)
    res.raise_for_status()
    articles = res.json()["articles"]

    urls = [article.get("url") for article in articles]
    return urls

def showArticle(url: str) -> tuple[str, str, str]:
    res = requests.get(url, timeout=30)
    res.raise_for_status()

    article = res.json()["article"]

    title = article["title"]
    html_body = article["body"]
    source_url = article["html_url"]
    article_id = article["id"]

    markdown_body = md(
        html_body,
        heading_style="ATX",
        bullets="-",
    )

    output = f"""# {title}
{markdown_body}
    """

    return output, article_id, title


# Calculate Hash
def get_content_hash(text : str):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Calculate Delta

def check_delta(file_name : str):
    return file_name in CHANGED_FILES

# Write to markdown function
    
def writeToMarkdown(output: str, article_id: str, title: str):
    out_dir = Path("scraped_articles")
    out_dir.mkdir(exist_ok=True)


    # If the file exists => Check the hash => if hash diff => update
    # => If not we keep it the same and move on.


    out_file = out_dir / f"{article_id}-{slugify(title)}.md"

    if out_file.exists():
        # Check hash first
        old_file_content = out_file.read_text(encoding="utf-8")

        existing_hash = get_content_hash(old_file_content)
        new_hash = get_content_hash(output)

        if existing_hash == new_hash:
            print(f"Skipping: {out_file.name} already exists!")
            RUN_COUNTS["skipped"] += 1
            return "skipped"
        else :
            out_file.write_text(output, encoding="utf-8")
            print(f"Saved: {out_file}")
            CHANGED_FILES.add(out_file.name)
            print(f"Appended to Delta: {out_file}")
            RUN_COUNTS["updated"] += 1
            return "updated"
            
        
    else :
        out_file.write_text(output, encoding="utf-8")
        print(f"Saved: {out_file}")
        CHANGED_FILES.add(out_file.name)
        print(f"Appended to Delta: {out_file}")
        RUN_COUNTS["added"] += 1
        return "added"
        


# Get the client GenAI

def getGenAIClient(api_key : str):
    return genai.Client(api_key=api_key)


def wait_for_operation(client, operation, file_name: str, poll_seconds: int = 5, timeout_seconds: int = 600):
    start_time = time.perf_counter()
    last_status = None

    while not operation.done:
        elapsed = time.perf_counter() - start_time
        if elapsed > timeout_seconds:
            raise TimeoutError(f"Indexing timed out after {timeout_seconds}s for {file_name}")

        metadata = getattr(operation, "metadata", None)
        status = f"metadata={metadata}" if metadata else "no progress metadata returned"

        if status != last_status:
            print(f"Indexing in progress for {file_name} ({elapsed:.0f}s elapsed, {status})...")
            last_status = status
        else:
            print(f"Indexing in progress for {file_name} ({elapsed:.0f}s elapsed)...")

        time.sleep(poll_seconds)

        try:
            operation = client.operations.get(operation)
        except errors.ServerError as exc:
            print(f"Polling failed with transient Gemini server error: {exc}")
            print("Keeping the same operation and retrying poll...")

    if getattr(operation, "error", None):
        raise RuntimeError(f"Indexing failed for {file_name}: {operation.error}")

    elapsed = time.perf_counter() - start_time
    print(f"Indexing completed for {file_name} in {elapsed:.0f}s.")
    return operation

# Get or create new vector Store

def get_or_create_vector_store(client, store_name : str) -> str :


    print(f"Searching for Vector store display name: '{store_name}'...")
    for store in client.file_search_stores.list():
        if getattr(store, "display_name", None) == store_name:
            print(f"Found existing store, reuse : {store.name}")
            return store.name
    
    print("No Store found, creating new one")

    new_store = client.file_search_stores.create(
        config= {
            "display_name": "Article Knowledge Base",
            "embedding_model": "models/gemini-embedding-2"
        }
    )
    print(f"Created new stored, ID: {new_store.name} ")
    return new_store.name

# Upload to vector store func

def upload_md_vector_store(client , dir_path: str = "scraped_articles"):

    vector_store_name = get_or_create_vector_store(client, "Article Knowledge Base")

    print(f"Store created successfully. ID/Name: {vector_store_name}\n")

    md_files = glob.glob(os.path.join(dir_path, "*.md"))

    if not md_files:
        print("No markdown files found in the specified directory.")
        return
    
    print(f"Found {len(md_files)} markdown files to upload.")

    uploaded_files_count = 0
    
    for file_path in md_files:
        file_name = os.path.basename(file_path)

        if not check_delta(file_name):
            print(f"⏩ [Delta-Skip]: {file_name} not changed. Skip upload.")
            continue

        print(f"Uploading and indexing: {file_name}...")
        
        # 2. Upload and automatically import/attach file to the Vector Store
        operation = client.file_search_stores.upload_to_file_search_store(
            file=file_path,
            file_search_store_name=vector_store_name,
            config={
                "display_name": file_name,
            }
        )
        
        # Wait for the embedding/indexing operation to complete.
        # Re-fetch the operation each poll so the done/error state is fresh.
        operation = wait_for_operation(client, operation, file_name)
            
        uploaded_files_count += 1
        RUN_COUNTS["uploaded"] += 1
        print(f"Finished processing {file_name}.\n")

    # 3. Log the final summary as requested in image_2933e7.png
    print("--- Ingestion Log Summary ---")
    print(f"Total Files Embedded: {uploaded_files_count}")
    print(f"added: {RUN_COUNTS['added']}")
    print(f"updated: {RUN_COUNTS['updated']}")
    print(f"skipped: {RUN_COUNTS['skipped']}")
    print(f"uploaded: {RUN_COUNTS['uploaded']}")
    print("Chunking Strategy: Managed automatically by Gemini File Search (Semantic Chunking).")
    print("-----------------------------")

    return vector_store_name


# Query the knowledge
def query_knowledge_base(client , store_name: str, user_question: str):
    # 1. Initialize the clien
    
    print(f"Querying store: {store_name}")
    print(f"Question: '{user_question}'\n")

    # 2. Call the model and pass your Vector Store as a FileSearch tool
    response = client.models.generate_content(
        model="gemini-3.5-flash",  # Or gemini-2.5-pro
        contents=user_question,
        config=types.GenerateContentConfig(
            system_instruction="""
You are OptiBot, the professional customer-support assistant for OptiSigns.com.

Strictly adhere to the following operational guidelines:
1. GROUNDING: Answer the user's question using ONLY the facts explicitly stated within the uploaded documents. Do not assume or use outside knowledge.
2. TONE: Maintain a helpful, factual, and highly concise tone.
3. LENGTH LIMIT: Provide your response in a maximum of 5 bullet points. If the explanation requires more detail, stop and immediately provide a link to the relevant document instead.
4. CITATION: You must cite the sources used. Include up to 3 "Article URL:" lines at the very end of your reply where applicable.
""",
            tools=[
                {
                    "file_search": {
                        "file_search_store_names": [store_name]
                    }
                }
            ]
        )
    )

    # 3. Print the grounded answer
    print("--- Assistant Response ---")
    print(response.text)
    print("--------------------------")


# Main function

def main():
    load_dotenv()
    # Load the API Key
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise RuntimeError("CRITICAL: The 'API_KEY' environment variable is not set.")

    client = getGenAIClient(api_key)
    # Requirement 1: Scrape and Save to Markdown
    urls = fetchArticlesUrl(30)

    for url in urls:
        output, article_id, title = showArticle(url)
        writeToMarkdown(output, article_id, title)

    # Requirement 2: Load markdown file to Vector Store
    
    vector_store_name = upload_md_vector_store(client=client, dir_path="scraped_articles")
    # print(vector_store_name)

    print(f"Vector store: {vector_store_name}")
    
    # Test the query
    # query_knowledge_base(client, vector_store_name, "How do I add a YouTube video?")
    # query_knowledge_base(client, vector_store_name, "How to access the Troubleshooting page of the OptiSigns Player")
    # query_knowledge_base(client, vector_store_name, "How to Play Licensed Background Music on Digital Signs with OptiSound")
    
if __name__ == "__main__":
    main()
