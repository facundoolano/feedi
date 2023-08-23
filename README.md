# feedi

TODO

## Local setup

## Advanced usage
### Bulk load feeds from csv

TODO

### Mastodon account setup

TODO

see https://mastodonpy.readthedocs.io/en/stable/#usage

### Kindle device setup

The app allows to register a kindle device (statically in the configuration, for now) to send the cleaned up article HTML to it. This uses the [stkclient](https://github.com/maxdjohnson/stkclient) library.

To setup the device in the config:

``` python
import stkclient

a = stkclient.OAuth2()
signin_url = a.get_signin_url()
# Open `signin_url` in a browser, sign in and authorize the application, pass
# the final redirect_url below
client = a.create_client(redirect_url)

with open('kindle.creds', 'w') as fp:
    client.dump(fp)
```



Then update [the config](https://github.com/facundoolano/feedi/blob/HEAD/feedi/config.py) to point to the credentials file:

    KINDLE_CREDENTIALS_PATH = 'kindle.creds'


### Keyboard shortcuts

TODO

## Design notes

TODO
