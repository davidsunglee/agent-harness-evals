# parse_duration

A small Python module that parses duration strings into seconds. Supported units:

| Suffix | Meaning |
| ------ | ------- |
| `s`    | seconds |
| `m`    | minutes |
| `h`    | hours   |

```python
from parse_duration import parse_duration

parse_duration("5s")   # 5
parse_duration("10m")  # 600
parse_duration("1h")   # 3600
```

## Tests

```sh
uv sync
uv run pytest -q tests/
```
