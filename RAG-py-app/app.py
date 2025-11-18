import streamlit as st
import os
import numpy as np
from typing import List, Tuple

# Using OpenAI integration - reference: python_openai blueprint
# The newest OpenAI model is "gpt-5" which was released August 7, 2025.
# Do not change this unless explicitly requested by the user
from openai import OpenAI

# Document processing
from pypdf import PdfReader
import faiss

# Initialize session state
if 'documents' not in st.session_state:
    st.session_state.documents = []
if 'embeddings' not in st.session_state:
    st.session_state.embeddings = None
if 'index' not in st.session_state:
    st.session_state.index = None
if 'chunks' not in st.session_state:
    st.session_state.chunks = []

# Configuration
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-5"

def get_openai_client():
    """Initialize OpenAI client with API key from environment."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)

def extract_text_from_pdf(pdf_file) -> str:
    """Extract text content from PDF file."""
    try:
        pdf_reader = PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:  # Guard against None values
                text += page_text + "\n"
        return text
    except Exception as e:
        st.error(f"Error reading PDF: {str(e)}")
        return ""

def extract_text_from_txt(txt_file) -> str:
    """Extract text from TXT file."""
    try:
        return txt_file.read().decode('utf-8')
    except Exception as e:
        st.error(f"Error reading text file: {str(e)}")
        return ""

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        end = start + chunk_size
        chunk = text[start:end]
        
        # Only add non-empty chunks
        if chunk.strip():
            chunks.append(chunk)
        
        start += chunk_size - overlap
    
    return chunks

def create_embeddings(client, texts: List[str]) -> np.ndarray:
    """Create embeddings for a list of texts using OpenAI API."""
    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=texts
        )
        embeddings = [item.embedding for item in response.data]
        return np.array(embeddings, dtype='float32')
    except Exception as e:
        st.error(f"Error creating embeddings: {str(e)}")
        return None

def build_faiss_index(embeddings: np.ndarray) -> faiss.IndexFlatL2:
    """Build FAISS index for similarity search."""
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings)
    return index

def search_similar_chunks(client, query: str, index: faiss.IndexFlatL2, chunks: List[str], k: int = 3) -> List[Tuple[str, float]]:
    """Search for most similar chunks to the query."""
    # Create embedding for query
    query_embedding = create_embeddings(client, [query])
    if query_embedding is None:
        return []
    
    # Search in FAISS index
    distances, indices = index.search(query_embedding, k)
    
    # Return chunks with their similarity scores
    results = []
    for idx, distance in zip(indices[0], distances[0]):
        if idx < len(chunks):
            results.append((chunks[idx], float(distance)))
    
    return results

def generate_answer(client, query: str, context_chunks: List[str]) -> str:
    """Generate answer using retrieved context chunks."""
    # Combine context chunks
    context = "\n\n".join([f"Context {i+1}:\n{chunk}" for i, chunk in enumerate(context_chunks)])
    
    # Create prompt
    prompt = f"""Based on the following context, please answer the question. If the answer cannot be found in the context, say so.

{context}

Question: {query}

Answer:"""
    
    try:
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that answers questions based on the provided context. Be concise and accurate."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"Error generating answer: {str(e)}")
        return None

# Streamlit UI
st.title("📚 Simple RAG System")
st.markdown("Upload documents and ask questions - the AI will answer using your document content!")

# Sidebar for document upload
with st.sidebar:
    st.header("📁 Document Upload")
    
    uploaded_files = st.file_uploader(
        "Upload your documents (PDF or TXT)",
        type=['pdf', 'txt'],
        accept_multiple_files=True,
        help="Upload one or more documents to build your knowledge base"
    )
    
    if uploaded_files:
        if st.button("Process Documents", type="primary"):
            client = get_openai_client()
            
            if not client:
                st.error("⚠️ OpenAI API key not found! Please add your OPENAI_API_KEY to use the RAG system.")
                st.info("The application is ready to use - just add your API key through the Secrets panel.")
            else:
                with st.spinner("Processing documents..."):
                    all_chunks = []
                    
                    # Process each uploaded file
                    for uploaded_file in uploaded_files:
                        if uploaded_file.name.endswith('.pdf'):
                            text = extract_text_from_pdf(uploaded_file)
                        else:
                            text = extract_text_from_txt(uploaded_file)
                        
                        if text:
                            chunks = chunk_text(text)
                            all_chunks.extend(chunks)
                            st.session_state.documents.append(uploaded_file.name)
                    
                    if all_chunks:
                        # Create embeddings
                        st.info(f"Creating embeddings for {len(all_chunks)} chunks...")
                        embeddings = create_embeddings(client, all_chunks)
                        
                        if embeddings is not None:
                            # Build FAISS index
                            index = build_faiss_index(embeddings)
                            
                            # Save to session state
                            st.session_state.chunks = all_chunks
                            st.session_state.embeddings = embeddings
                            st.session_state.index = index
                            
                            st.success(f"✅ Successfully processed {len(uploaded_files)} document(s) into {len(all_chunks)} chunks!")
    
    # Display processed documents
    if st.session_state.documents:
        st.divider()
        st.subheader("📄 Loaded Documents")
        for doc in st.session_state.documents:
            st.text(f"• {doc}")
        st.caption(f"Total chunks: {len(st.session_state.chunks)}")
        
        if st.button("Clear All Documents"):
            st.session_state.documents = []
            st.session_state.embeddings = None
            st.session_state.index = None
            st.session_state.chunks = []
            st.rerun()

# Main area for Q&A
st.header("💬 Ask Questions")

if not st.session_state.chunks:
    st.info("👈 Upload and process documents in the sidebar to get started!")
else:
    client = get_openai_client()
    
    if not client:
        st.warning("⚠️ OpenAI API key not configured. Add your OPENAI_API_KEY to ask questions.")
    else:
        # Question input
        question = st.text_input(
            "Ask a question about your documents:",
            placeholder="e.g., What is the main topic discussed in the documents?"
        )
        
        if question:
            with st.spinner("Searching for relevant information..."):
                # Find similar chunks
                similar_chunks = search_similar_chunks(
                    client,
                    question,
                    st.session_state.index,
                    st.session_state.chunks,
                    k=3
                )
                
                if similar_chunks:
                    # Generate answer
                    context_texts = [chunk for chunk, _ in similar_chunks]
                    
                    with st.spinner("Generating answer..."):
                        answer = generate_answer(client, question, context_texts)
                    
                    if answer:
                        # Display answer
                        st.subheader("🤖 Answer")
                        st.write(answer)
                        
                        # Display sources
                        st.divider()
                        st.subheader("📖 Source Chunks")
                        st.caption("These document chunks were used to generate the answer:")
                        
                        for i, (chunk, distance) in enumerate(similar_chunks):
                            with st.expander(f"Source {i+1} (similarity score: {distance:.4f})"):
                                st.text(chunk)

# Footer
st.divider()
st.caption("Built with Streamlit, OpenAI, and FAISS • Simple RAG System")
