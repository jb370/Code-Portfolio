# Simple RAG System

## Overview
A Retrieval-Augmented Generation (RAG) system built with Python and Streamlit that allows users to upload documents and ask questions about them. The system uses OpenAI's embeddings and language models to provide accurate, context-aware answers based on the uploaded content.

## Recent Changes
**November 18, 2025**: Initial implementation of complete RAG pipeline
- Implemented document upload and processing (PDF and TXT support)
- Added text chunking with configurable overlap
- Integrated OpenAI embeddings (text-embedding-3-small) for vector representations
- Implemented FAISS vector store for efficient similarity search
- Created Q&A interface with GPT-5 for answer generation
- Added source transparency - displays which document chunks were used for answers
- Fixed critical bugs: PDF None value handling and OpenAI API parameter correction

## Project Architecture

### Technology Stack
- **Framework**: Streamlit (web interface)
- **Embeddings**: OpenAI text-embedding-3-small
- **LLM**: OpenAI GPT-5
- **Vector Store**: FAISS (Facebook AI Similarity Search)
- **Document Processing**: pypdf for PDF parsing
- **Python Version**: 3.11

### Core Components

1. **Document Processing Pipeline**
   - Supports PDF and TXT file uploads
   - Extracts text content with error handling
   - Chunks text into 500-character segments with 50-character overlap
   - Guards against None values from PDF extraction

2. **Embedding & Vector Storage**
   - Generates embeddings using OpenAI's text-embedding-3-small model
   - Stores vectors in FAISS IndexFlatL2 for L2 distance similarity search
   - Session-based storage for current user session

3. **Retrieval & Generation**
   - Converts user queries into embeddings
   - Retrieves top-k (default: 3) most relevant document chunks
   - Passes retrieved context to GPT-5 for answer generation
   - Returns both answer and source chunks for transparency

4. **User Interface**
   - Sidebar: Document upload and management
   - Main area: Question input and answer display
   - Source visibility: Shows which chunks were used to generate answers
   - Graceful handling of missing API key

### File Structure
- `app.py` - Main Streamlit application with complete RAG implementation
- `.streamlit/config.toml` - Streamlit server configuration (port 5000)
- `pyproject.toml` - Python dependencies managed by UV package manager

## Configuration

### Required Environment Variables
- `OPENAI_API_KEY` - OpenAI API key for embeddings and chat completions

### RAG Parameters
- Chunk Size: 500 characters
- Chunk Overlap: 50 characters
- Embedding Model: text-embedding-3-small
- Chat Model: GPT-5
- Retrieval Count: Top 3 similar chunks
- Max Tokens: 1000 for answer generation

## Workflow
The application runs on port 5000 using the command:
```
streamlit run app.py --server.port 5000
```

## User Preferences
None specified yet.

## Known Limitations
- Document storage is session-based (cleared on refresh)
- No persistent database for documents
- Maximum context limited by token constraints
- Currently supports only PDF and TXT formats
- Type hints show LSP warnings but don't affect runtime
