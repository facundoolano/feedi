"""
Module to ingest a logged user's timeline to include in the app main feed.
"""
import json
import logging

import mastodon

logger = logging.getLogger(__name__)


def fetch_avatar(server_url, access_token):
    client = mastodon.Mastodon(access_token=access_token,
                               api_base_url=server_url)
    return client.me()['avatar']


# TODO add better logging here
def fetch_toots(server_url, access_token, newer_than=None, limit=None):
    client = mastodon.Mastodon(access_token=access_token,
                               api_base_url=server_url)

    if newer_than:
        # get all pages of toots more recent than the given id
        results = client.timeline(min_id=newer_than)
        toots = results
        while results:
            results = client.fetch_previous(results)
            toots += results

    elif limit:
        # get all pages of toots until reaching the limit or exhausting the list
        results = client.timeline(limit=limit)
        toots = results
        while results and len(toots) < limit:
            results = client.fetch_next(results)
            toots += results

    else:
        raise ValueError("expected either limit or newer_than argument")

    values = []
    for toot in toots:
        try:
            values.append(parse_values(server_url, toot))
        except:
            logger.exception("error parsing toot")

    return values


def parse_values(server_url, toot):
    """
    Translate any toot api result data into the format expected by the local Entry model.
    """

    result = {
        'raw_data': json.dumps(toot, default=str)
    }

    # the updated date is taken from the base toot, so if if it's a reblog it will be the time
    # it was reblogged. This will be used for sorting entries in the timeline.
    # in that case the created date, the one displayed, will be taken from the reblogged toot
    result['remote_updated'] = toot['edited_at'] or toot['created_at']

    if toot.get('reblog'):
        result['reblogged_by'] = toot['account']['display_name']
        toot = toot['reblog']

    result['title'] = toot['account']['display_name']
    result['avatar_url'] = toot['account']['avatar']
    result['username'] = toot['account']['acct']
    result['body'] = toot['content']
    result['remote_id'] = toot['id']
    result['remote_created'] = toot['created_at']
    result['content_url'] = toot['url']

    # we typically want to open the logged in user account's instance, not the original mastodon instance,
    # so we build the local url (which doesn't seem to come in the api response)
    result['user_url'] = f'{server_url}/@{toot["account"]["acct"]}'
    result['entry_url'] = f'{result["user_url"]}/{toot["id"]}'

    # for media we only support images for now and will take just the first one
    media = [m['preview_url'] for m in toot['media_attachments'] if m['type'] == 'image']
    if media:
        result['media_url'] = media[0]
    elif toot['card']:
        # NOTE: ideally we'd like to include more info in the embed, not just the preview image. e.g. title, description.
        result['media_url'] = toot['card'].get('image')

    return result
