\# Pensieve Project Rules



\## Scope

Current work is frontend presentation only.



Allowed:

\- frontend UI

\- layout

\- CSS

\- visual components

\- animations

\- responsive design

\- presentation-only interactions



Forbidden unless explicitly authorized:

\- backend/

\- database

\- API contracts

\- authentication

\- retrieval logic

\- LLM logic

\- classification logic

\- date parsing

\- inspiration rendering backend

\- Docker / deployment config

\- startup scripts



\## Safety Rules



Do not modify backend files.



Do not change:

\- API URLs

\- HTTP methods

\- request payloads

\- response field names

\- database schemas

\- existing business logic



Prefer the smallest possible frontend-only change.



Before editing:

1\. run git status

2\. check current branch

3\. inspect relevant frontend files

4\. identify the minimum required files



After editing:

1\. run git diff

2\. verify no backend files changed

3\. verify no API contract changed

4\. verify existing features still work



Do not:

\- commit

\- push

\- merge

\- rebase

\- reset --hard

\- force push



unless explicitly instructed.



If a UI requirement requires backend changes, stop and explain what backend change would be required instead of making it.

