from fastapi import FastAPI, UploadFile, File
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter  # <-- Changed import
import supabase
import os
import io

app = FastAPI()

# Test route to verify server is working
@app.get("/")
def read_root():
    return {"message": "DocTalk AI Backend is Liverrr!"}

# The core PDF processing pipeline
@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    print(f"📥 Received file: {file.filename}")
    
    # 1. Read PDF
    contents = await file.read()
    
    # 2. Extract text with pypdf
    pdf_text = ""
    reader = PdfReader(io.BytesIO(contents))
    for page in reader.pages:
        pdf_text += page.extract_text() + "\n"
    
    print(f"📄 Extracted {len(pdf_text)} characters of text")
    
    # 3. Chunk text with LangChain
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )
    chunks = text_splitter.split_text(pdf_text)
    
    print(f"✂️ Split into {len(chunks)} chunks")
    print(f"First chunk: {chunks[0][:100]}...")
    
    return {
        "filename": file.filename,
        "text_length": len(pdf_text),
        "chunk_count": len(chunks),
        "first_chunk_preview": chunks[0][:100] + "..."
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)