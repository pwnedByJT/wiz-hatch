## Summary

Describe the user-visible behavior, motivation, and operational impact.

## Validation

- [ ] `python -m ruff format --check .`
- [ ] `python -m ruff check .`
- [ ] `python -m mypy`
- [ ] `python -m pytest`
- [ ] Container or manifest changes were tested on ARM64 or an equivalent build

## Security and release checklist

- [ ] No secrets, tokens, personal data, or sensitive logs are included
- [ ] New inputs are validated and no new runtime network calls occur during autocomplete
- [ ] Dependencies and GitHub Actions are pinned and justified
- [ ] Version changes follow the sequential Semantic Versioning policy
- [ ] Generated `data/pets.json` changes were reviewed for schema and provenance

