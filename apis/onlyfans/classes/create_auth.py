import asyncio
import math
from datetime import datetime
from itertools import chain, product
from typing import Any, Optional, Union

import jsonpickle
from apis import api_helper
from apis.onlyfans.classes.create_message import create_message
from apis.onlyfans.classes.create_post import create_post
from apis.onlyfans.classes.create_user import create_user
from apis.onlyfans.classes.extras import (auth_details, content_types,
                                          create_headers, endpoint_links,
                                          error_details, handle_refresh)
from dateutil.relativedelta import relativedelta
from user_agent import generate_user_agent


class create_auth:
    def __init__(
        self,
        option={},
        pool=None,
        max_threads=-1,
    ) -> None:
        self.id = option.get("id")
        self.username: str = option.get("username")
        if not self.username:
            self.username = f"u{self.id}"
        self.name = option.get("name")
        self.email: str = option.get("email")
        self.lists = {}
        self.links = content_types()
        self.isPerformer: bool = option.get("isPerformer")
        self.chatMessagesCount = option.get("chatMessagesCount")
        self.subscribesCount = option.get("subscribesCount")
        self.subscriptions: list[create_user] = []
        self.chats = None
        self.archived_stories = {}
        self.mass_messages = []
        self.paid_content = []
        self.session_manager = api_helper.session_manager(self, max_threads=max_threads)
        self.pool = pool
        self.auth_details: Optional[auth_details] = None
        self.cookies: dict = {}
        self.profile_directory = option.get("profile_directory", "")
        self.guest = False
        self.active = False
        self.errors: list[error_details] = []
        self.extras = {}

    def update(self, data):
        for key, value in data.items():
            found_attr = hasattr(self, key)
            if found_attr:
                setattr(self, key, value)

    async def login(self, full=False, max_attempts=10, guest=False):
        auth_version = "(V1)"
        if guest:
            self.auth_details.auth_id = "0"
            self.auth_details.user_agent = generate_user_agent()
        auth_items = self.auth_details
        link = endpoint_links().customer
        user_agent = auth_items.user_agent
        auth_id = str(auth_items.auth_id)
        x_bc = auth_items.x_bc
        # expected string error is fixed by auth_id
        auth_cookies = [
            {"name": "auth_id", "value": auth_id},
            {"name": "sess", "value": auth_items.sess},
            {"name": "auth_hash", "value": auth_items.auth_hash},
            {"name": f"auth_uniq_{auth_id}", "value": auth_items.auth_uniq_},
            {"name": f"auth_uid_{auth_id}", "value": None},
        ]
        dynamic_rules = self.session_manager.dynamic_rules
        a = [dynamic_rules, auth_id, user_agent, x_bc, auth_items.sess, link]
        self.session_manager.headers = create_headers(*a)
        if guest:
            print("Guest Authentication")
            return self
        for session in self.session_manager.sessions:
            for auth_cookie in auth_cookies:
                session.cookies.set(**auth_cookie)

        self.cookies = {d["name"]: d["value"] for d in auth_cookies}
        count = 1
        while count < max_attempts + 1:
            string = f"Auth {auth_version} Attempt {count}/{max_attempts}"
            print(string)
            await self.get_authed()
            count += 1

            async def resolve_auth(auth: create_auth):
                if self.errors:
                    error = self.errors[-1]
                    print(error.message)
                    if error.code == 101:
                        if auth_items.support_2fa:
                            link = f"https://onlyfans.com/api2/v2/users/otp/check"
                            count = 1
                            max_count = 3
                            while count < max_count + 1:
                                print(
                                    "2FA Attempt " + str(count) + "/" + str(max_count)
                                )
                                code = input("Enter 2FA Code\n")
                                data = {"code": code, "rememberMe": True}
                                r = await self.session_manager.json_request(
                                    link, method="POST", payload=data
                                )
                                if "error" in r:
                                    error.message = r["error"]["message"]
                                    count += 1
                                else:
                                    print("Success")
                                    auth.active = True
                                    auth.errors.remove(error)
                                    break

            await resolve_auth(self)
            if not self.active:
                if self.errors:
                    error = self.errors[-1]
                    error_message = error.message
                    if "token" in error_message:
                        break
                    if "Code wrong" in error_message:
                        break
                    if "Please refresh" in error_message:
                        break
                else:
                    print("Auth 404'ed")
                continue
            else:
                print(f"Welcome {self.name} | {self.username}")
                break
        return self

    async def get_authed(self):
        if not self.active:
            link = endpoint_links().customer
            r = await self.session_manager.json_request(link, sleep=False)
            if r:
                self.resolve_auth_errors(r)
                if not self.errors:
                    self.active = True
                    self.update(r)
            else:
                # 404'ed
                self.active = False
        return self

    def resolve_auth_errors(self, r):
        # Adds an error object to self.auth.errors
        if "error" in r:
            error = r["error"]
            error_message = r["error"]["message"]
            error_code = error["code"]
            error = error_details()
            if error_code == 0:
                pass
            elif error_code == 101:
                error_message = "Blocked by 2FA."
            elif error_code == 401:
                # Session/Refresh
                pass
            error.code = error_code
            error.message = error_message
            self.errors.append(error)
        else:
            self.errors.clear()

    async def get_lists(self, refresh=True, limit=100, offset=0):
        api_type = "lists"
        if not self.active:
            return
        if not refresh:
            subscriptions = handle_refresh(self, api_type)
            return subscriptions
        link = endpoint_links(global_limit=limit, global_offset=offset).lists
        results = await self.session_manager.json_request(link)
        self.lists = results
        return results

    async def get_user(self, identifier: Union[str, int]):
        link = endpoint_links(identifier).users
        result = await self.session_manager.json_request(link)
        result["session_manager"] = self.session_manager
        result = create_user(result,self) if "error" not in result else result
        return result

    async def get_lists_users(
        self, identifier, check: bool = False, refresh=True, limit=100, offset=0
    ):
        if not self.active:
            return
        link = endpoint_links(
            identifier, global_limit=limit, global_offset=offset
        ).lists_users
        results = await self.session_manager.json_request(link)
        if len(results) >= limit and not check:
            results2 = await self.get_lists_users(
                identifier, limit=limit, offset=limit + offset
            )
            results.extend(results2)
        return results

    async def get_subscription(
        self, check: bool = False, identifier="", limit=100, offset=0
    ) -> Union[create_user, None]:
        subscriptions = await self.get_subscriptions(refresh=False)
        valid = None
        for subscription in subscriptions:
            if identifier == subscription.username or identifier == subscription.id:
                valid = subscription
                break
        return valid

    async def get_subscriptions(
        self,
        resume=None,
        refresh=True,
        identifiers: list = [],
        extra_info=True,
        limit=20,
        offset=0,
    ) -> list[create_user]:
        if not self.active:
            return []
        if not refresh:
            subscriptions = self.subscriptions
            return subscriptions
        link = endpoint_links(global_limit=limit, global_offset=offset).subscriptions
        ceil = math.ceil(self.subscribesCount / limit)
        a = list(range(ceil))
        offset_array = []
        for b in a:
            b = b * limit
            link = endpoint_links(global_limit=limit, global_offset=b).subscriptions
            offset_array.append(link)

        # Following logic is unique to creators only
        results = []
        if self.isPerformer:
            temp_session_manager = self.session_manager
            temp_pool = self.pool
            temp_paid_content = self.paid_content
            delattr(self, "session_manager")
            delattr(self, "pool")
            delattr(self, "paid_content")
            json_authed = jsonpickle.encode(self, unpicklable=False)
            json_authed = jsonpickle.decode(json_authed)
            self.session_manager = temp_session_manager
            self.pool = temp_pool
            self.paid_content = temp_paid_content
            temp_auth = await self.get_user(self.username)
            json_authed = json_authed | temp_auth.__dict__

            subscription = create_user(json_authed,self)
            subscription.subscriber = self
            subscription.subscribedByData = {}
            new_date = datetime.now() + relativedelta(years=1)
            subscription.subscribedByData["expiredAt"] = new_date.isoformat()
            subscription = [subscription]
            results.append(subscription)
        if not identifiers:

            async def multi(item):
                link = item
                # link = item["link"]
                # session = item["session"]
                subscriptions = await self.session_manager.json_request(link)
                valid_subscriptions = []
                extras = {}
                extras["auth_check"] = ""
                if isinstance(subscriptions, str):
                    input(subscriptions)
                    return
                subscriptions = [
                    subscription
                    for subscription in subscriptions
                    if "error" != subscription
                ]
                for subscription in subscriptions:
                    subscription["session_manager"] = self.session_manager
                    if extra_info:
                        subscription2 = await self.get_user(subscription["username"])
                        if isinstance(subscription2, dict):
                            if "error" in subscription2:
                                continue
                        subscription = subscription | subscription2.__dict__
                    subscription = create_user(subscription,self)
                    subscription.session_manager = self.session_manager
                    subscription.subscriber = self
                    valid_subscriptions.append(subscription)
                return valid_subscriptions

            pool = self.pool
            # offset_array= offset_array[:16]
            tasks = pool.starmap(multi, product(offset_array))
            results += await asyncio.gather(*tasks)
        else:
            for identifier in identifiers:
                if self.id == identifier or self.username == identifier:
                    continue
                link = endpoint_links(identifier=identifier).users
                result = await self.session_manager.json_request(link)
                if "error" in result or not result["subscribedBy"]:
                    continue
                subscription = create_user(result,self)
                subscription.session_manager = self.session_manager
                subscription.subscriber = self
                results.append([subscription])
                print
            print
        results = [x for x in results if x is not None]
        results = list(chain(*results))
        self.subscriptions = results
        return results

    async def get_chats(
        self,
        links: Optional[list] = None,
        limit=100,
        offset=0,
        refresh=True,
        inside_loop=False,
    ) -> list:
        api_type = "chats"
        if not self.active:
            return []
        if not refresh:
            result = handle_refresh(self, api_type)
            if result:
                return result
        if links is None:
            links = []
        api_count = self.chatMessagesCount
        if api_count and not links:
            link = endpoint_links(
                identifier=self.id, global_limit=limit, global_offset=offset
            ).list_chats
            ceil = math.ceil(api_count / limit)
            numbers = list(range(ceil))
            for num in numbers:
                num = num * limit
                link = link.replace(f"limit={limit}", f"limit={limit}")
                new_link = link.replace("offset=0", f"offset={num}")
                links.append(new_link)
        multiplier = self.session_manager.pool._processes
        if links:
            link = links[-1]
        else:
            link = endpoint_links(
                identifier=self.id, global_limit=limit, global_offset=offset
            ).list_chats
        links2 = api_helper.calculate_the_unpredictable(link, limit, multiplier)
        if not inside_loop:
            links += links2
        else:
            links = links2
        results = await self.session_manager.async_requests(links)
        has_more = results[-1]["hasMore"]
        final_results = [x["list"] for x in results]
        final_results = list(chain.from_iterable(final_results))

        if has_more:
            results2 = await self.get_chats(
                links=[links[-1]], limit=limit, offset=limit + offset, inside_loop=True
            )
            final_results.extend(results2)

        final_results.sort(key=lambda x: x["withUser"]["id"], reverse=True)
        self.chats = final_results
        return final_results

    async def get_mass_messages(
        self, resume=None, refresh=True, limit=10, offset=0
    ) -> list:
        api_type = "mass_messages"
        if not self.active:
            return []
        if not refresh:
            result = handle_refresh(self, api_type)
            if result:
                return result
        link = endpoint_links(
            global_limit=limit, global_offset=offset
        ).mass_messages_api
        results = await self.session_manager.json_request(link)
        items = results.get("list", [])
        if not items:
            return items
        if resume:
            for item in items:
                if any(x["id"] == item["id"] for x in resume):
                    resume.sort(key=lambda x: x["id"], reverse=True)
                    self.mass_messages = resume
                    return resume
                else:
                    resume.append(item)

        if results["hasMore"]:
            results2 = self.get_mass_messages(
                resume=resume, limit=limit, offset=limit + offset
            )
            items.extend(results2)
        if resume:
            items = resume

        items.sort(key=lambda x: x["id"], reverse=True)
        self.mass_messages = items
        return items

    async def get_paid_content(
        self,
        check: bool = False,
        refresh: bool = True,
        limit: int = 99,
        offset: int = 0,
        inside_loop: bool = False,
    ) -> list[Union[create_message, create_post]]:
        api_type = "paid_content"
        if not self.active:
            return []
        if not refresh:
            result = handle_refresh(self, api_type)
            if result:
                return result
        link = endpoint_links(global_limit=limit, global_offset=offset).paid_api
        final_results = await self.session_manager.json_request(link)
        if len(final_results) >= limit and not check:
            results2 = self.get_paid_content(
                limit=limit, offset=limit + offset, inside_loop=True
            )
            final_results.extend(results2)
        if not inside_loop:
            temp = []
            for final_result in final_results:
                content = None
                if final_result["responseType"] == "message":
                    user = create_user(final_result["fromUser"],self)
                    content = create_message(final_result,user)
                    print
                elif final_result["responseType"] == "post":
                    user = create_user(final_result["author"],self)
                    content = create_post(final_result, user)
                if content:
                    temp.append(content)
            final_results = temp
        self.paid_content = final_results
        return final_results
