"""Print a new random master key for encrypting stored OAuth tokens.

Put the output in your .env as GSOI_MASTER_KEY=... (development) or in your secret manager.
Losing the key means stored connections can no longer be decrypted (you would reconnect).
"""

from cryptography.fernet import Fernet

print(Fernet.generate_key().decode())
