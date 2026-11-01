"""One-shot, main-process-invoked provider setup operations (JSON stdout)."""
import argparse
import json
import os
import sys

from openai import OpenAI


def run(operation, base_url, model):
    key = os.environ.get("AI_COMMENTATOR_PROVIDER_API_KEY", "")
    if not key:
        raise ValueError("Provider credential is missing.")
    client = OpenAI(api_key=key, base_url=base_url, timeout=20.0, max_retries=0)
    try:
        if operation == "models":
            response = client.models.list()
            models = []
            for item in getattr(response, "data", [])[:500]:
                name = getattr(item, "id", None)
                if isinstance(name, str) and name and len(name) <= 200:
                    models.append({"id": name, "vision": "unverified"})
            return {"ok": True, "models": models}
        if operation == "connection":
            client.models.list()
            return {"ok": True, "connected": True}
        if operation == "verify-vision":
            # A tiny valid inline image intentionally tests image input. This
            # is an explicit provider request and may incur provider charges.
            response = client.chat.completions.create(
                model=model,
                max_tokens=8,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": "Reply with the word OK."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j0ioAAAAASUVORK5CYII="}},
                ]}],
            )
            if not getattr(response, "choices", None):
                raise ValueError("Provider returned no completion for the vision check.")
            return {"ok": True, "visionVerified": True}
        raise ValueError("Unsupported provider operation.")
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("connection", "models", "verify-vision"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args()
    try:
        result = run(args.operation, args.base_url, args.model)
    except Exception:
        # Provider exception bodies can contain headers or credentials.
        result = {"ok": False, "error": "Provider request failed. Check the URL, key, model, and provider compatibility."}
        print(json.dumps(result), flush=True)
        return 1
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
