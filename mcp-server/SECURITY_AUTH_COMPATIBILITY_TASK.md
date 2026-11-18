# MCP authentication compatibility follow-up

Work only in mcp-server/. Do not modify files outside mcp-server/. Do not stage, commit, or push. Preserve all current work.

## Review finding
The new Git child environment deliberately sets GIT_CONFIG_GLOBAL=os.devnull and GIT_CONFIG_NOSYSTEM=1. This correctly blocks hostile global/system settings and the new tests pass. However, it also suppresses credential.helper entries configured only in a user's global or system Git config. Preserving SSH_AUTH_SOCK and GCM_* environment variables alone does not guarantee Git Credential Manager is invoked. The current tests do not exercise real credential-helper selection or an authenticated push.

## Goal
Retain the global/system configuration isolation, but provide a safe, explicit way for the operator to opt into a known Git credential helper without loading the rest of their global/system Git configuration.

## Requirements
1. Inspect Git's supported credential-helper configuration mechanism and current MCP server code.
2. Do not re-enable global or system Git configuration wholesale.
3. Add an explicit operator-controlled setting for a credential helper used only by Git push operations. A launch-time environment variable or CLI option is acceptable; document the choice and secure defaults.
4. Accept only a strict, documented set of helper identifiers or safely resolved executable names. Do not accept arbitrary shell commands, bang-prefixed helpers, config fragments, path arguments, or values from the repository's .git/config as the trusted helper setting.
5. Keep SSH agent and credential-manager environment variables intact. Do not log helper output, credentials, tokens, or secret values.
6. Apply the helper only to push subprocesses. Read-only Git commands, commit previews and commits must not gain an extra credential helper setting.
7. Preserve GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM=1, pager/diff protections, exact Git command grammars, push confirmation, timeouts and output limits.
8. If a safe cross-platform implementation cannot be established, do not weaken global config isolation. Explain the limitation clearly and leave credential helper support explicitly opt-in/unavailable instead.

## Tests
- Unit-test that an unset helper setting does not inject a credential helper.
- Reject arbitrary command strings, shell metacharacters, bang-prefixed helpers, and unknown identifiers.
- Test that a documented approved helper is injected for push only.
- Test read-only commands and commits do not receive helper config.
- Use temporary fake credential-helper executables and sanitized test environments. Never contact a real remote, read real user config, or use real credentials.
- Ensure test output does not contain fake or real secret values.
- Preserve all current security tests, including global hooks isolation and local repository hook execution.

## Verification
From mcp-server/:
- python -m unittest discover -s tests -v
- python -m compileall -q src tests

From repository root:
- git diff --check

Do not stage, commit, or push.

## Report
Describe the opt-in setting, accepted helper identifiers, how push-only behavior is enforced, tests and results, and any remaining limitations. Never claim authenticated pushing was integration-tested unless it actually was.