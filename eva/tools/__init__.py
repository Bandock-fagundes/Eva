"""Registro de ferramentas (as 'mãos' da Eva). Para criar uma nova, use @tool num arquivo desta pasta
e importe o módulo no fim deste arquivo."""
TOOLS = {}


def tool(name: str, description: str, params: dict, required=None):
    def deco(fn):
        TOOLS[name] = {
            "fn": fn,
            "schema": {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {"type": "object", "properties": params,
                                   "required": list(params) if required is None else required},
                },
            },
        }
        return fn
    return deco


def schemas():
    return [t["schema"] for t in TOOLS.values()]


from . import basics, web, files, shell, browser  # noqa: E402,F401  (registra as ferramentas)
