import logging
import os
from typing import Optional, Union, TYPE_CHECKING

import dash
from authlib.integrations.flask_client import OAuth
from dash_auth.auth import Auth
from flask import redirect, request, session, url_for, has_request_context

if TYPE_CHECKING:
    from authlib.integrations.flask_client.apps import (
        FlaskOAuth1App, FlaskOAuth2App
    )


class OIDCAuth(Auth):
    """Implements auth via OpenID."""

    def __init__(
        self,
        app: dash.Dash,
        secret_key: str = Optional[None],
        force_https_callback: Optional[Union[bool, str]] = None,
        client_kwargs: Optional[dict] = None,
        authorize_redirect_kwargs: Optional[dict] = None,
        authorize_token_kwargs: Optional[dict] = None,
        login_route: str = "/oidc/login",
        logout_route: str = "/oidc/logout",
        callback_route: str = "/oidc/callback",
        log_signins: bool = False,
        public_routes: Optional[list] = None,
        **kwargs,
    ):
        """Secure a Dash app through OpenID Connect.

        Parameters
        ----------
        app : Dash
            The Dash app to secure
        secret_key : str, optional
            A string to protect the Flask session, by default None.
            Generate a secret key in your Python session
            with the following commands:
            >>> import os
            >>> import base64
            >>> base64.b64encode(os.urandom(30)).decode('utf-8')
            Note that you should not do this dynamically:
            you should create a key and then assign the value of
            that key in your code.
        force_https_callback : Union[bool, str], optional
            Whether to force redirection to https, by default None
            This is useful when the HTTPS termination is upstream of the server
            If a string is passed, this will check for the existence of
            an envvar with that name and force https callback if it exists.
        client_kwargs : dict, optional
            Keyword arguments passed to the OAuth client
        authorize_redirect_kwargs : dict, optional
            Keyword arguments passed to the authorize_redirect function
        authorize_token_kwargs : dict, optional
            Keyword arguments passed to the authorize_access_token
        login_route : str, optional
            The route for the login function, by default "/oidc/login".
        logout_route : str, optional
            The route for the logout function, by default "/oidc/logout".
        callback_route : str, optional
            The route for the OIDC redirect URI, by default "/oidc/callback".
        log_signins : bool, optional
            Whether to log signins, by default False
        **kwargs
            Additional keyword arguments are passed to oauth.register.

        Raises
        ------
        Exception
            Raise an exception if the app.server.secret_key is not defined
        """
        super().__init__(app, public_routes=public_routes)
        if force_https_callback is not None:
            self.force_https_callback = (
                os.getenv(force_https_callback) is not None
                if isinstance(force_https_callback, str)
                else force_https_callback
            )
        else:
            self.force_https_callback = False

        client_kwargs = client_kwargs or {}
        client_kwargs.setdefault("scope", "openid email")
        self.client_kwargs = client_kwargs
        self.authorize_redirect_kwargs = authorize_redirect_kwargs or {}
        self.authorize_token_kwargs = authorize_token_kwargs or {}
        self.login_route = login_route
        self.logout_route = logout_route
        self.callback_route = callback_route
        self.log_signins = log_signins

        if secret_key is not None:
            app.server.secret_key = secret_key

        if app.server.secret_key is None:
            raise RuntimeError(
                """
                app.server.secret_key is missing.
                Generate a secret key in your Python session
                with the following commands:
                >>> import os
                >>> import base64
                >>> base64.b64encode(os.urandom(30)).decode('utf-8')
                and assign it to the property app.server.secret_key
                (where app is your dash app instance), or pass is as
                the secret_key argument to OIDCAuth.__init__.
                Note that you should not do this dynamically:
                you should create a key and then assign the value of
                that key in your code/via a secret.
                """
            )

        super().__init__(app)

        oauth = OAuth(app.server)
        self.oidc: Union[FlaskOAuth1App, FlaskOAuth2App] = oauth.register(
            "oidc", client_kwargs=client_kwargs, **kwargs
        )

        app.server.add_url_rule(
            login_route, view_func=self.login_request, methods=["GET"]
        )
        app.server.add_url_rule(
            logout_route, view_func=self.logout, methods=["GET"]
        )
        app.server.add_url_rule(
            callback_route, view_func=self.callback, methods=["GET"]
        )

    def login_request(self):
        """Login the user."""
        kwargs = {"_external": True}
        if self.force_https_callback:
            kwargs["_scheme"] = "https"
        if request.headers.get("X-Forwarded-Host"):
            host = request.headers.get("X-Forwarded-Host")
            redirect_uri = f"https://{os.path.join(host, self.callback_route)}"
        else:
            redirect_uri = url_for(self.callback_route, **kwargs)

        return self.oidc.authorize_redirect(
            redirect_uri, **self.authorize_redirect_kwargs
        )

    def logout(self):  # pylint: disable=C0116
        """Logout the user."""
        session.clear()
        return redirect(self.app.config.get("url_base_pathname") or "/")

    def callback(self):  # pylint: disable=C0116
        """Do the OIDC dance."""
        token = self.oidc.authorize_access_token(**self.authorize_token_kwargs)
        user = token.get("userinfo")
        if "offline_access" in self.client_kwargs["scope"]:
            refresh_token = token.get("refresh_token")
        if user:
            session["user"] = user
            session["refresh_token"] = refresh_token
            if self.log_signins:
                logging.info("User %s is logging in.", user.get("email"))

        return redirect(self.app.config.get("url_base_pathname") or "/")

    def is_authorized(self):  # pylint: disable=C0116
        """Check whether ther user is authenticated."""
        return request.path == self.callback_route or "user" in session


import re
from numbers import Number
from typing import Callable, Literal, Optional, Union
from dash.development.base_component import Component as DashComponent
from dash.exceptions import PreventUpdate

ComponentPart = Union[DashComponent, str, Number]
Component = Union[ComponentPart, list[ComponentPart]]
OutputVal = Union[Callable[[], Component], Component]
CheckType = Literal["one_of", "all_of", "none_of"]


def list_groups(
    *,
    groups_key: str = "groups",
    groups_str_split: str = None,
) -> Optional[list[str]]:
    """List all the groups the user belongs to.

    :param groups_key: Groups key in the user data saved in the Flask session
        e.g. session["user"] == {"email": "a.b@mail.com", "groups": ["admin"]}
    :param groups_str_split: Used to split groups if provided as a string
    :return: None or list[str]:
        * None if the user is not authenticated
        * list[str] otherwise
    """
    if not has_request_context() or "user" not in session:
        return None

    user_groups = session.get("user", {}).get(groups_key, [])
    # Handle cases where groups are ,- or ;-separated string,
    # may depend on OIDC provider
    if isinstance(user_groups, str) and groups_str_split is not None:
        user_groups = re.split(groups_str_split, user_groups)
    return user_groups


def check_groups(
    groups: Optional[list[str]] = None,
    *,
    groups_key: str = "groups",
    groups_str_split: str = None,
    check_type: CheckType = "one_of",
) -> Optional[bool]:
    """Check whether the current user is authenticated
    and has the specified groups.

    :param groups: List of groups to check for with check_type
    :param groups_key: Groups key in the user data saved in the Flask session
        e.g. session["user"] == {"email": "a.b@mail.com", "groups": ["admin"]}
    :param groups_str_split: Used to split groups if provided as a string
    :param check_type: Type of check to perform.
        Either "one_of", "all_of" or "none_of"
    :return: None or boolean:
        * None if the user is not authenticated
        * True if the user is authenticated and has the right permissions
        * False if the user is authenticated but does not have
          the right permissions
    """
    user_groups = list_groups(
        groups_key=groups_key,
        groups_str_split=groups_str_split,
    )

    if user_groups is None:
        # User is not authenticated
        return None

    if groups is None:
        return True

    if check_type == "one_of":
        return bool(set(user_groups).intersection(groups))
    if check_type == "all_of":
        return all(group in user_groups for group in groups)
    if check_type == "none_of":
        return not any(group in user_groups for group in groups)

    raise ValueError(f"Invalid check_type: {check_type}")


def protected(
    unauthenticated_output: Optional[OutputVal] = None,
    *,
    missing_permissions_output: Optional[OutputVal] = None,
    groups: Optional[list[str]] = None,
    groups_key: str = "groups",
    groups_str_split: str = None,
    check_type: CheckType = "one_of",
) -> Callable:
    """Decorate a function or output to alter it depending on the state
    of authentication and permissions.

    :param unauthenticated_output: Output when the user is not authenticated.
        Note: needs to be a function with no argument or
        a collection of Dash components.
    :param missing_permissions_output: Output when the user is authenticated
        but does not have the right permissions.
        It defaults to unauthenticated_output when not set.
        Note: needs to be a function with no argument or
        a collection of Dash components.
    :param groups: List of authorized user groups. If no groups are passed,
        the decorator will only check whether the user is authenticated.
    :param groups_key: Groups key in the user data saved in the Flask session
        e.g. session["user"] == {"email": "a.b@mail.com", "groups": ["admin"]}
    :param groups_str_split: Used to split groups if provided as a string
    :param check_type: Type of check to perform.
        Either "one_of", "all_of" or "none_of"
    """
    if unauthenticated_output is None:
        unauthenticated_output = ""

    if missing_permissions_output is None:
        missing_permissions_output = unauthenticated_output

    def decorator(output: OutputVal):
        def wrap(*args, **kwargs):
            def process_output(output, *args, **kwargs):
                if isinstance(output, Callable):
                    return output(*args, **kwargs)
                return output

            authorized = check_groups(
                groups=groups,
                groups_key=groups_key,
                groups_str_split=groups_str_split,
                check_type=check_type,
            )
            if authorized is None:
                return process_output(unauthenticated_output)
            if authorized:
                return process_output(output, *args, **kwargs)
            return process_output(missing_permissions_output)

        if isinstance(output, Callable):
            return wrap
        return wrap()

    return decorator


def protected_callback(
    *callback_args,
    groups: list[str] = None,
    groups_key: str = "groups",
    groups_str_split: str = None,
    check_type: CheckType = "one_of",
    **callback_kwargs,
) -> Callable:
    """Protected Dash callback.

    :param **: all args and kwargs passed to a dash callback
    :param groups: List of authorized user groups
    :param groups_key: Groups key in the user data saved in the Flask session
        e.g. session["user"] == {"email": "a.b@mail.com", "groups": ["admin"]}
    :param groups_str_split: Used to split groups if provided as a string
    :param check_type: Type of check to perform.
        Either "one_of", "all_of" or "none_of"
    """

    def decorator(func):
        def prevent_unauthenticated():
            logging.info(
                "A user tried to run %s without being authenticated.",
                func.__name__,
            )
            raise PreventUpdate

        def prevent_unauthorised():
            logging.info(
                "%s tried to run %s but did not have the right permissions.",
                session["user"]["email"],
                func.__name__,
            )
            raise PreventUpdate

        wrapped_func = dash.callback(*callback_args, **callback_kwargs)(
            protected(
                unauthenticated_output=prevent_unauthenticated,
                missing_permissions_output=prevent_unauthorised,
                groups=groups,
                groups_key=groups_key,
                groups_str_split=groups_str_split,
                check_type=check_type,
            )(func)
        )

        def wrap(*args, **kwargs):
            return wrapped_func(*args, **kwargs)

        return wrap

    return decorator
