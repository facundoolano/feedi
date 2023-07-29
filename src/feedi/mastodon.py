"""
Module to ingest a logged user's timeline to include in the app main feed.
Assumes an app has been registered and an a user logged in and credentials made available in the environment.

This could eventually be extended to include an Oauth login flow in the front-end, as well
as supporting multiple account log in.
"""
import mastodon

# TODO verify whether the access token is long lived or requires refresh


def fetch_avatar(server_url, access_token):
    client = mastodon.Mastodon(access_token=access_token,
                               api_base_url=server_url)
    return client.me()['avatar']


def fetch_toots(older_than=None, newer_than=None):
    client = mastodon.Mastodon(access_token=access_token,
                               api_base_url=server_url)

    args = {}
    if older_than:
        max_id = older_than
    elif newer_than:
        # TODO we could potentially want to keep requesting for new toots until the response
        # is empty, when pulling for updates. For now going with a conservative single page
        min_id = newer_than

    toots = client.timeline(**args)


def translate_to_entry(toot):
    pass
