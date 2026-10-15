import re
import html
from typing import Dict

class MissingPlaceholderError(Exception):
    """Raised when a placeholder in the template cannot be matched to any column."""
    pass

def substitute(template: str, data: Dict[str, str], is_html: bool) -> str:
    """
    Substitutes placeholders in the form {{ Column Name }} with values from data.
    - Matches are case-insensitive and trimmed.
    - If is_html is True, values are HTML-escaped.
    - Raises MissingPlaceholderError if a placeholder is not found in data.
    """
    # Regex: {{ followed by optional whitespace, then non-curly-brace chars, then optional whitespace, then }}
    pattern = r"{{\s*([^{}]+?)\s*}}"

    def replace_match(match):
        placeholder = match.group(1).strip()

        # Case-insensitive search for the key in data
        found_key = None
        for key in data.keys():
            if key.strip().lower() == placeholder.lower():
                found_key = key
                break

        if found_key is None:
            raise MissingPlaceholderError(f"Placeholder '{{{{ {placeholder} }}}}' not found in recipient data.")

        value = data[found_key]

        if is_html:
            return html.escape(value)
        return value

    return re.sub(pattern, replace_match, template)
