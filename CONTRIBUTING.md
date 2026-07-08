# Contributing

Thank you for your interest in improving SkillSpector Report.

## Ways to Contribute

You can help by:

- reporting bugs
- suggesting improvements
- improving documentation
- testing the tool with different SkillSpector scan outputs
- opening pull requests for small fixes or focused improvements

## Before Opening a Pull Request

Please run the local checks before opening a pull request:

    ruff check .
    bandit -r src --severity-level medium
    pip-audit

If you change PDF generation behavior, please also generate and review example reports before submitting the change.

## Project Scope

SkillSpector Report is an independent companion tool for NVIDIA SkillSpector.

It does not replace NVIDIA SkillSpector, does not perform independent security analysis, and is not affiliated with, endorsed by, or sponsored by NVIDIA.

Please avoid changes that imply affiliation with or endorsement by NVIDIA.

## Security Issues

Please do not report sensitive security issues in public GitHub issues.

See `SECURITY.md` for the security reporting policy.
