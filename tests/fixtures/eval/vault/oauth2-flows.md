# OAuth2 Authorization Code Flow

The OAuth2 authorization code grant lets a user authorize an application without
sharing their password. The app redirects the user to the authorization server,
which authenticates them and returns a short-lived authorization code. The app
exchanges that code, plus its client secret, for an access token at the token
endpoint. PKCE adds a code verifier and challenge so public clients like mobile apps
complete the flow safely without a stored secret.
