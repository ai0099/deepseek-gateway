import httpx, json, asyncio

async def main():
    async with httpx.AsyncClient(timeout=90) as c:
        # Test 1: Basic non-streaming
        body = {"model": "gpt-5.6-sol", "stream": False,
                "input": [{"role": "user", "content": "say hello only"}],
                "reasoning": {"effort": "max"}}
        r = await c.post("http://127.0.0.1:8080/v1/responses", json=body)
        print(f"Test1 non-stream: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            for o in data.get("output", []):
                t = o.get("type")
                if t == "reasoning":
                    print(f"  reasoning: {len(o.get('summary', [{}])[0].get('text', ''))} chars")
                elif t == "message":
                    print(f"  message: ok")
                else:
                    print(f"  {t}: ok")

        # Test 2: With tools
        body["tools"] = [{"type": "shell", "name": "shell", "description": "run cmd",
                          "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}]
        body["tool_choice"] = "auto"
        r = await c.post("http://127.0.0.1:8080/v1/responses", json=body)
        print(f"Test2 with tools: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            for o in data.get("output", []):
                print(f"  {o.get('type')}: ok")
        else:
            print(f"  Error: {r.text[:200]}")

asyncio.run(main())
