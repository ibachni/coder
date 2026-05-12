from pathlib import Path
from jinja2 import FileSystemLoader, Environment, StrictUndefined

env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "prompts"), undefined=StrictUndefined
)


def render(template_name: str, **kwargs: object) -> str:
    return env.get_template(f"{template_name}.j2").render(**kwargs)
