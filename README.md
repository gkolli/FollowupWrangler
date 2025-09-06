# FollowupWrangler
Follow-Up Wrangler is a CLI tool that sweeps radiology PDFs, extracts incidental findings and follow-up recommendations using Gemini + OCR, and outputs structured tasks, summaries, and risk dashboards. Doctors and IT can then query results in natural language to close critical follow-up gaps

## What it does:

| **Reason**                                                      | **Consequence**                                                                       | **How Follow-Up Wrangler Helps**                                                  |
| --------------------------------------------------------------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Focus on primary clinical question (e.g., “rule out pneumonia”) | Incidental findings only described in *Findings* section, not flagged in *Impression* | Sweeps entire report and surfaces hidden recommendations or uncaptured findings   |
| Ambiguity / fear of over-calling                                | Radiologists avoid suggesting follow-ups for borderline lesions                       | Standardizes extraction into structured tasks without adding radiologist workload |
| Time pressure in ED / trauma                                    | No follow-up note added due to volume and speed                                       | Automates detection in seconds, no manual effort                                  |
| Lack of clear guidelines across body parts                      | Inconsistent documentation of recommendations                                         | Normalizes language + adds due dates into structured CSV                          |
| EHR / workflow gap (PDFs not actionable)                        | Even when recs exist, they don’t convert into orders → \~17% follow-up in ED          | Converts buried lines into tasks, summaries, and risk dashboards                  |


## Demo image:
<img width="1416" height="845" alt="Screenshot 2025-09-06 at 3 37 06 PM" src="https://github.com/user-attachments/assets/2f239e42-4f2c-4ea1-8b84-8a3e027f558e" />

