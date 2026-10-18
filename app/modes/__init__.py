"""Each mode is an async generator of JSON-serialisable events that the API streams as NDJSON.

Event types shared by all modes (from runner.py):
  token  {model, kind: "content"|"thinking", text, ...tag}
  done   {model, content, stats, ...tag}
  error  {model, error, ...tag}
"""
