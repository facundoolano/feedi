"""
Module to ingest a logged user's timeline to include in the app main feed.
"""
import json
import logging

import mastodon

logger = logging.getLogger(__name__)

CLIENT_NAME = 'feedi'
SCOPES = ['read', 'write']


def register_app(server_url, callback_url):
    return mastodon.Mastodon.create_app(CLIENT_NAME, api_base_url=server_url,
                                        redirect_uris=[callback_url],
                                        scopes=SCOPES)


def auth_redirect_url(server_url, client_id, client_secret, callback_url):
    client = mastodon.Mastodon(client_id=client_id,
                               client_secret=client_secret,
                               api_base_url=server_url)
    return client.auth_request_url(client_id=client_id, scopes=SCOPES,
                                   redirect_uris=callback_url)


def oauth_login(server_url, client_id, client_secret, callback_url, code):
    client = mastodon.Mastodon(client_id=client_id,
                               client_secret=client_secret,
                               api_base_url=server_url)
    return client.log_in(code=code, redirect_uri=callback_url, scopes=SCOPES)


def fetch_account_data(server_url, access_token):
    client = mastodon.Mastodon(access_token=access_token,
                               api_base_url=server_url)
    return client.me()


def boost(server_url, access_token, toot_id):
    client = mastodon.Mastodon(access_token=access_token,
                               api_base_url=server_url)
    client.status_reblog(id=toot_id)


def favorite(server_url, access_token, toot_id):
    client = mastodon.Mastodon(access_token=access_token,
                               api_base_url=server_url)
    client.status_favourite(id=toot_id)


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

        if toot.get('in_reply_to_id') and not toot.get('reblog'):
            # we don't want to show replies as standalone toots in the timeline, unless they are reblogs
            continue

        if toot.get('reblog'):
            reblogged_by = display_name(toot)
            entry['header'] = f'<i class="fas fa-retweet"></i> { reblogged_by } boosted'
            toot = toot['reblog']

        entry['avatar_url'] = toot['account']['avatar']
        entry['username'] = toot['account']['acct']
        entry['display_name'] = display_name(toot)
        entry['body'] = toot['content']
        entry['remote_id'] = toot['id']
        entry['remote_created'] = toot['created_at']

        # we don't want toots to be expanded on the local reader, so we exclude content_url
        # this could change if we started to add stuff like displaying (or adding) comments
        # result['content_url'] = toot['url']

        # use server-local urls
        entry['entry_url'] = status_url(server_url, toot)

        # for media we only support images for now and will take just the first one
        media = [m['preview_url'] for m in toot['media_attachments'] if m['type'] == 'image']
        if media:
            entry['media_url'] = media[0]
        elif toot['card']:
            # NOTE: ideally we'd like to include more info in the embed, not just the preview image. e.g. title, description.
            entry['media_url'] = toot['card'].get('image')

        # show (read-only) poll options
        if toot.get('poll'):
            entry['body'] += '<ul>'
            for option in toot['poll']['options']:
                entry['body'] += f'<li>{option["title"]}</li>'
            entry['body'] += '</ul>'

        entries.append(entry)

    return entries


def fetch_notifications(server_url, access_token, newer_than=None, limit=None):
    notifications = mastodon_request(server_url, 'notifications', access_token, newer_than, limit)
    entries = []
    for notification in notifications:
        NOTIFICATION_PHRASES = {
            "mention": ('fa-comment-alt', "mentioned you"),
            "status": ('fa-comment-alt', "posted"),
            "reblog": ('fa-retweet', 'reblogged a post'),
            "follow": ('fa-user-plus', "followed you"),
            "follow_request": ('fa-user-plus', "requested to follow you"),
            "favourite": ('fa-star', "favorited a post"),
        }
        # NOTE: ignoring these notification types
        # poll = A poll you have voted in or created has ended
        # update = A status you interacted with has been edited
        # admin.sign_up = Someone signed up (optionally sent to admins)
        # admin.report = A new report has been filed
        if notification['type'] not in NOTIFICATION_PHRASES:
            continue

        (icon, phrase) = NOTIFICATION_PHRASES[notification["type"]]
        body = f'<i class="fas {icon}"></i> {display_name(notification)} {phrase}'

        entry = {
            'remote_id': notification['id'],
            'remote_updated': notification['created_at'],
            'remote_created': notification['created_at'],
            'raw_data': json.dumps(notification, default=str),
            'avatar_url': notification['account']['avatar'],
            'username': notification['account']['acct'],
            'display_name': display_name(notification),
            'body': body}

        # NOTE: we could attempt to render the source toot in the body as the mastodon web ui does,
        # but I'm guessing that more often than not that would result in useless messages spamming the feed.
        # leaving it empty and relying on the entry_url / title link to get to the source status
        if notification['type'] in ['follow', 'follow_request']:
            entry['entry_url'] = user_url(server_url, notification)
        else:
            entry['entry_url'] = status_url(server_url, notification['status'])

        entries.append(entry)

    return entries


def user_url(server_url, status_dict):
    """
    Return the url of the given status author in the given server.
    (as opposed of the user url in their own mastodon instance).
    """
    return f'{server_url}/@{status_dict["account"]["acct"]}'


def status_url(server_url, status_dict):
    "Return the url of the given status local to the given server."
    return f'{user_url(server_url, status_dict)}/{status_dict["id"]}'


def display_name(status_dict):
    return status_dict['account']['display_name'] or status_dict['account']['acct'].split('@')[0]


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
