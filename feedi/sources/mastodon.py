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


def fetch_toots(server_url, access_token, newer_than=None, limit=None):
    toots = mastodon_request(server_url, 'timeline', access_token, newer_than, limit)
    entries = []
    for toot in toots:
        entry = {
            'raw_data': json.dumps(toot, default=str)
        }

        # the updated date is taken from the base toot, so if if it's a reblog it will be the time
        # it was reblogged. This will be used for sorting entries in the timeline.
        # in that case the created date, the one displayed, will be taken from the reblogged toot
        entry['remote_updated'] = toot['edited_at'] or toot['created_at']

        if toot.get('reblog'):
            reblogged_by = toot['account']['display_name']
            entry['reblogged_by'] = f'<i class="fas fa-retweet"></i> { reblogged_by } boosted'
            toot = toot['reblog']

        entry['title'] = toot['account']['display_name']
        entry['avatar_url'] = toot['account']['avatar']
        entry['username'] = toot['account']['acct']
        entry['body'] = toot['content']
        entry['remote_id'] = toot['id']
        entry['remote_created'] = toot['created_at']

        # we don't want toots to be expanded on the local reader, so we exclude content_url
        # this could change if we started to add stuff like displaying (or adding) comments
        # result['content_url'] = toot['url']

        # we typically want to open the logged in user account's instance, not the original mastodon instance,
        # so we build the local url (which doesn't seem to come in the api response)
        entry['user_url'] = f'{server_url}/@{toot["account"]["acct"]}'
        entry['entry_url'] = f'{entry["user_url"]}/{toot["id"]}'

        # for media we only support images for now and will take just the first one
        media = [m['preview_url'] for m in toot['media_attachments'] if m['type'] == 'image']
        if media:
            entry['media_url'] = media[0]
        elif toot['card']:
            # NOTE: ideally we'd like to include more info in the embed, not just the preview image. e.g. title, description.
            entry['media_url'] = toot['card'].get('image')

        entries.append(entry)

    return entries


def fetch_notifications(server_url, access_token, newer_than=None, limit=None):
    notifications = mastodon_request(server_url, 'notifications', access_token, newer_than, limit)
    entries = []
    for notification in notifications:
        NOTIFICATION_PHRASES = {
            "mention": "mentioned you",
            "status": "posted",
            "reblog": "reblogged",
            "follow": "followed you",
            "follow_request": "requested to follow you",
            "favourite": "favorited",
        }
        # NOTE: ignoring these notification types
        # poll = A poll you have voted in or created has ended
        # update = A status you interacted with has been edited
        # admin.sign_up = Someone signed up (optionally sent to admins)
        # admin.report = A new report has been filed
        if notification['type'] not in NOTIFICATION_PHRASES:
            continue
        display_name = notification['account']['display_name']
        header_text = f'{display_name} {NOTIFICATION_PHRASES[notification["type"]]}'

        entry = {
            'id': notification['id'],
            'remote_updated': notification['created_at'],
            'remote_created': notification['created_at'],
            'raw_data': json.dumps(notification, default=str),
            'user_url': f'{server_url}/@{notification["account"]["acct"]}',
            'avatar_url': notification['account']['avatar'],
            'username': notification['account']['acct'],
            'title': display_name,
            'reblogged_by': header_text}

        # NOTE: we could attempt to render the source toot in the body as the mastodon web ui does,
        # but I'm guessing that more often than not that would result in useless messages spamming the feed.
        # leaving it empty and relying on the entry_url / title link to get to the source status
        if notification['type'] in ['follow', 'follow_request']:
            entry['entry_url'] = entry['user_url']
        else:
            entry['entry_url'] = f'{entry["user_url"]}/{notification["status"]["id"]}'

        entries.append(entry)

    return entries


def mastodon_request(server_url, method, access_token, newer_than=None, limit=None):
    client = mastodon.Mastodon(access_token=access_token,
                               api_base_url=server_url)

    if newer_than:
        # get all pages of toots more recent than the given id
        results = getattr(client, method)(min_id=newer_than)
        items = results
        while results:
            results = client.fetch_previous(results)
            items += results

    elif limit:
        # get all pages of toots until reaching the limit or exhausting the list
        results = getattr(client, method)(limit=limit)
        items = results
        while results and len(items) < limit:
            results = client.fetch_next(results)
            items += results

    else:
        raise ValueError("expected either limit or newer_than argument")

    return items
