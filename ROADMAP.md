# ChatVector-AI Development Roadmap

This document outlines the current development focus and future direction of the ChatVector-AI project. It is intended for contributors to quickly understand priorities and pick up tasks.

---

##  Fun Beginner-Friendly Issues 

These issues are perfect for first-time contributors. They're well-scoped, have clear instructions, and let you add some personality to the project while exploring the codebase, project setup and collaborative GitHub PR process.

**Note:** No need to understand the codebase or RAG concepts to contribute here!

| Issue | What You'll Build | Impact | Skill Level |
|-------|-------------------|--------------|-------------|
| [#65 Add MIT License Badge to README](https://github.com/chatvector-ai/chatvector-ai/issues/65) | Add a simple "MIT License" badge next to the license section | Makes license info instantly visible and adds polish | Beginner |
| [#61 Add Makefile with Custom Commands for Docker](https://github.com/chatvector-ai/chatvector-ai/issues/61) | Create a Makefile with memorable shortcuts like `make startcv`, `make logs`, `make help` | Save everyone keystrokes and feel like a command-line wizard | Beginner |
| [#60 Add System Status Endpoint with ASCII Health Meter](https://github.com/chatvector-ai/chatvector-ai/issues/60) | Build a `/status` endpoint that returns an ASCII art health meter (📊) | Get creative with ASCII art while building something useful | Beginner |
| [#58 Add Setup Demo Video to Quick Links](https://github.com/chatvector-ai/chatvector-ai/issues/58) | Record a short setup demo and add it to the README | Be the face of the project and help future contributors get started | Beginner |
| [#57 Create Interactive Star Progress Bar in README](https://github.com/chatvector-ai/chatvector-ai/issues/57) | Add a GitHub-style progress bar showing our star goal | Watch the bar fill up as the community grows! | Beginner |
| [#64 Add Quick Links Badges to README for Better Navigation](https://github.com/chatvector-ai/chatvector-ai/issues/64) | Turn boring text links into eye-catching badges with icons | Make the README pop and help visitors find resources faster | Beginner |

** All beginner issues are tagged with** `good first issue` • `beginner-friendly` • `first-timers-only`

---

##  Phase 1: Stabilize & Optimize Core Engine (Current)

- **Focus:** Hardening the RAG backend for **reliability, observability, and performance**. 
- **Emphasis:** Advanced logging, ingestion pipeline robustness, asynchronous and batch processing foundations, chunking quality, and comprehensive error handling. 
- Completion of these tasks is critical before starting Phase 2.

### Core Reliability

| Issue | Description | Skill Level |
|-------|-------------|-------------|
| [#44 Add Retry Logic to Database Batch Inserts](https://github.com/chatvector-ai/chatvector-ai/issues/44) | Make database inserts resilient with retry logic (matching our embedding retries). Currently the weak link in ingestion pipeline. | Intermediate |
| [#43 Validation & error handling in upload pipeline](https://github.com/chatvector-ai/chatvector-ai/issues/43) | Add comprehensive input validation and graceful error handling during document upload | Intermediate |
| [#46 Upload Progress / Status Tracking](https://github.com/chatvector-ai/chatvector-ai/issues/46) | Implement real-time progress tracking for large document uploads | Intermediate |
| [#63 Add Transaction/Rollback Support to Upload Pipeline](https://github.com/chatvector-ai/chatvector-ai/issues/63) | Ensure atomic uploads - if chunk insertion fails, don't leave orphaned document records. Critical for data integrity. | Advanced |

### Performance & Observability

| Issue | Description | Skill Level |
|-------|-------------|-------------|
| [#20 Implement JSON logging format](https://github.com/chatvector-ai/chatvector-ai/issues/20) | Add JSON-formatted logging option for better integration with log aggregation tools | Beginner |
| [#22 Research centralized logging integration](https://github.com/chatvector-ai/chatvector-ai/issues/22) | Investigate options for shipping logs to services like DataDog, Splunk, or ELK | Research |
| [#31 Async / batch retrieval for chat endpoint](https://github.com/chatvector-ai/chatvector-ai/issues/31) | Optimize chat responses by retrieving and processing context in parallel | Advanced |
| [#28 Implement Embedding Queue for Production Scaling](https://github.com/chatvector-ai/chatvector-ai/issues/28) | Build background queue system to handle embedding generation at scale without rate limits or timeouts | Advanced |

### RAG Enhancements (Answer Quality)

| Issue | Description | Skill Level |
|-------|-------------|-------------|
| [#23 Enhance chunk metadata storage](https://github.com/chatvector-ai/chatvector-ai/issues/23) | Store additional metadata (page numbers, headings, timestamps) with each chunk | Beginner |
| [#24 Normalize PDF text in ingestion pipeline](https://github.com/chatvector-ai/chatvector-ai/issues/24) | Clean up extracted PDF text (fix line breaks, normalize whitespace, handle special characters) | Beginner |
| [#25 Tune chunk size & overlap](https://github.com/chatvector-ai/chatvector-ai/issues/25) | Experiment with different chunk sizes and overlaps to optimize retrieval quality | Beginner |
| [#26 Add source citation support](https://github.com/chatvector-ai/chatvector-ai/issues/26) | Make LLM responses include references to source documents and specific chunks | Beginner |

###  Documentation

| Issue | Description | Skill Level |
|-------|-------------|-------------|
| [#45 Align terminology: "Ingestion" vs "Upload"](https://github.com/chatvector-ai/chatvector-ai/issues/45) | Standardize on consistent terminology throughout the codebase and docs | Beginner |

---

##  Frontend

The frontend is the face of ChatVector and demonstrates what's possible. It's designed to:
- **Showcase demos** of different RAG use cases (research assistant, contract analysis, knowledge base)
- **Provide a testing ground** for developers to experiment with the API
- **Inspire adoption** by showing real-world examples
- **Eventually host community contributions** of creative implementations

While the backend is the core engine, the frontend is the face of ChatVector - and we want it to be pretty! These issues are perfect for frontend devs who want to work on UI/UX and gain experience building features that connect with backend APIs.

| Issue | Description | Skill Level |
|-------|-------------|-------------|
| [#2 Create chat page layout](https://github.com/chatvector-ai/chatvector-ai/issues/2) | Build the basic UI structure for the chat interface (message bubbles, input field, send button) | Beginner |
| [#4 Add navigation menu with routing](https://github.com/chatvector-ai/chatvector-ai/issues/4) | Implement navigation between chat, upload, and settings pages | Beginner |
| [#5 Add document upload to chat page](https://github.com/chatvector-ai/chatvector-ai/issues/5) | Integrate the upload component into the chat interface | Beginner |

**Future frontend vision:** ChatVector showcase/information/docs. Multiple demo pages showing different implementation ex., community-contributed examples, and interactive tutorials.

---

##  Phase 2: Enhance Developer Experience (Next)

Focus: Make ChatVector-AI easier to adopt, extend, and integrate.

* Developer tools: Client SDKs, deployment improvements (e.g., Docker)
* Advanced caching: Redis for embedding and response caching
* Extended RAG features: Advanced chunking, query transformations, prompt tuning
* **Frontend expansion:** Add demo gallery, use case examples, interactive playground

---

## Phase 3: Scale & Specialize (Later)

Focus: Production-ready document intelligence platform.

* Enterprise features: Authentication, multi-tenancy, monitoring
* Specialized pipelines: Legal, academic, or code documents
* Ecosystem growth: Community integrations, example applications
* **Frontend maturity:** Full documentation site with live API explorer, community showcase gallery

---

###  Notes

* "Good First Issue" tags highlight tasks suitable for new contributors.
* **New contributors should start with the 🎮 Fun Beginner-Friendly Issues section!**
* Issue status (blocked, in progress, etc.) is visible when you click through to GitHub.
* This roadmap provides a quick overview; click any link for full issue details.
* #28 is critical for production scaling - ensures embedding generation doesn't become a bottleneck under load.