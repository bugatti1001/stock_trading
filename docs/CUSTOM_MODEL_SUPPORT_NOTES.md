# Custom OpenAI-Compatible Model Support Notes

Goal: add support for the fixed NVIDIA OpenAI-compatible model provider.

Example shape:

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://inference-api.nvidia.com",
    api_key="<user-provided-api-key>",
)

resp = client.chat.completions.create(
    model="azure/openai/gpt-5.4",
    messages=[{"role": "user", "content": "hi, please reply OK"}],
    max_tokens=20,
)
```

Config fields needed:

- `nvidia_api_key`
- `ai_provider = nvidia`

The base URL and model are intentionally hardcoded:

- Base URL: `https://inference-api.nvidia.com`
- Model: `azure/openai/gpt-5.4`

Likely code paths:

- `backend/app/config/settings.py`: read NVIDIA API key from `UserSetting`, with `NVIDIA_API_KEY` environment fallback.
- `backend/app/services/ai_client.py`: add `nvidia` provider and route it through the existing OpenAI-compatible client helper using the hardcoded base URL.
- `backend/app/auth.py` and `backend/app/templates/login.html`: capture and persist the NVIDIA API key during login.
- `backend/app/api/agent_routes.py`: include `nvidia` in provider validation and availability checks.
- `backend/app/templates/base.html`: add NVIDIA to the provider switcher.

Do not commit real API keys. Store only placeholders in docs and `.env.example`.
