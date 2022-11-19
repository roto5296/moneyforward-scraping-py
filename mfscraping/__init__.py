import datetime
import re
import time
import urllib

import requests
from bs4 import BeautifulSoup as BS
from requests.exceptions import HTTPError, Timeout

from .exceptions import DataDoesNotExist, FetchTimeout, LoginFailed, MFConnectionError


class MFScraper:
    def __init__(self, id, passwd, timeout=10):
        self._id = id
        self._passwd = passwd
        self._session = requests.session()
        self._timeout = timeout

    def login(self):
        try:
            result = self._session.get("https://moneyforward.com/sign_in/", timeout=self._timeout)
            result.raise_for_status()
            qs = urllib.parse.urlparse(result.url).query
            qs_d = urllib.parse.parse_qs(qs)
            soup = BS(result.content, "html.parser")
            token = soup.find("meta", {"name": "csrf-token"})["content"]
            post_data = {
                "authenticity_token": token,
                "_method": "post",
                "mfid_user[email]": self._id,
                "mfid_user[password]": self._passwd,
                "select_account": "true",
            }
            post_data.update(qs_d)
            result = self._session.post(
                "https://id.moneyforward.com/sign_in", data=post_data, timeout=self._timeout
            )
            result.raise_for_status()
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)
        if result.url != "https://moneyforward.com/":
            raise LoginFailed

    def fetch(self, delay=2, maxwaiting=300):
        try:
            result = self._session.get("https://moneyforward.com", timeout=self._timeout)
            result.raise_for_status()
            soup = BS(result.content, "html.parser")
            urls = soup.select("a[data-remote=true]")
            urls = [url["href"] for url in urls]
            token = soup.select_one("meta[name=csrf-token]")["content"]
            headers = {
                "Accept": "text/javascript",
                "X-CSRF-Token": token,
                "X-Requested-With": "XMLHttpRequest",
            }
            self._results = []
            for url in urls:
                self._session.post(
                    "https://moneyforward.com" + url, headers=headers, timeout=self._timeout
                ).raise_for_status()
            counter = 0
            while counter < maxwaiting:
                time.sleep(delay)
                counter += delay
                result = self._session.get(
                    "https://moneyforward.com/accounts/polling", timeout=self._timeout
                )
                result.raise_for_status()
                if not result.json()["loading"]:
                    return
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)
        raise FetchTimeout

    def get(self, year, month):
        try:
            result = self._session.get("https://moneyforward.com", timeout=self._timeout)
            result.raise_for_status()
            soup = BS(result.content, "html.parser")
            token = soup.select_one("meta[name=csrf-token]")["content"]
            headers = {
                "Accept": "text/javascript",
                "X-CSRF-Token": token,
                "X-Requested-With": "XMLHttpRequest",
            }
            post_data = {
                "from": str(year) + "/" + str(month) + "/1",
                "service_id": "",
                "account_id_hash": "",
            }
            result = self._session.post(
                "https://moneyforward.com/cf/fetch",
                data=post_data,
                headers=headers,
                timeout=self._timeout,
            )
            result.raise_for_status()
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)
        search_result = re.search(r'\$\("\.list_body"\)\.append\((.*?)\);', result.text)
        if search_result is None:
            raise DataDoesNotExist
        html = search_result.group(1)
        html = eval(html).replace("\\", "")
        soup = BS(html, "html.parser")
        trs = soup.select("tr")
        ret = []
        for tr in trs:
            if "icon-ban-circle" in str(tr):
                continue
            transaction_id = int(tr["id"].replace("js-transaction-", ""))
            td_date = tr.select_one("td.date").text.replace("\n", "")
            date = datetime.date(year, int(td_date[0:2]), int(td_date[3:5]))
            td_amount = tr.select_one("td.amount").text.replace("\n", "")
            is_transfer = "振替" in td_amount
            amount = int(re.sub("[^0-9-]", "", td_amount))
            td_calc = tr.select_one("td.calc[style]")
            for sel in td_calc.select("select"):
                sel.clear()
            if is_transfer:
                to = td_calc.select_one("div.transfer_account_box").extract()
                account = [td_calc.text.replace("\n", ""), to.text.replace("\n", "")]
            else:
                account = td_calc.text.replace("\n", "")
            transaction = {
                "transaction_id": transaction_id,
                "date": date,
                "amount": abs(amount) if is_transfer else amount,
                "account": account,
                "lcategory": tr.select_one("td.lctg").text.replace("\n", ""),
                "mcategory": tr.select_one("td.mctg").text.replace("\n", ""),
                "content": tr.select_one("td.content").text.replace("\n", ""),
                "memo": tr.select_one("td.memo").text.replace("\n", ""),
                "is_transfer": is_transfer,
            }
            ret.append(transaction)
        ret = sorted(ret, key=lambda x: (x["date"], x["transaction_id"]), reverse=True)
        return ret

    def get_account(self):
        try:
            result = self._session.get("https://moneyforward.com", timeout=self._timeout)
            result.raise_for_status()
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)
        soup = BS(result.content, "html.parser")
        accounts = {}
        for a in soup.select("#registered-manual-accounts li.account a[href^='/accounts/show']"):
            accounts.update(
                {
                    a.text: {
                        "is_editable": True,
                        "moneyforward_id": a["href"].replace("/accounts/show_manual/", ""),
                    }
                }
            )
        for a in soup.select("#registered-accounts li.account a[href^='/accounts/show']"):
            accounts.update(
                {
                    a.text: {
                        "is_editable": False,
                        "moneyforward_id": a["href"].replace("/accounts/show/", ""),
                    }
                }
            )
        for a in soup.select("#user_asset_act_sub_account_id_hash option"):
            try:
                accounts[a.text.strip(" ")]["edit_id"] = a["value"]
            except KeyError:
                continue
        return accounts

    def get_category(self):
        try:
            result = self._session.get("https://moneyforward.com/cf", timeout=self._timeout)
            result.raise_for_status()
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)
        soup = BS(result.content, "html.parser")
        categories = {}
        css_list = ["ul.dropdown-menu.main_menu.plus", "ul.dropdown-menu.main_menu.minus"]
        keys = ["plus", "minus"]
        for (css, key) in zip(css_list, keys):
            d_pm = {}
            c_pm = soup.select_one(css)
            for l_c in c_pm.select("li.dropdown-submenu"):
                d = {m_c.text: {"id": int(m_c["id"])} for m_c in l_c.select("a.m_c_name")}
                tmp = l_c.select_one("a.l_c_name")
                d.update({"id": int(tmp["id"])})
                d_pm.update({tmp.text: d})
            categories.update({key: d_pm})
        return categories

    def save(
        self,
        date,
        amount,
        account,
        lcategory="未分類",
        mcategory="未分類",
        content="",
        is_transfer=False,
    ):
        try:
            result = self._session.get("https://moneyforward.com/cf", timeout=self._timeout)
            result.raise_for_status()
            soup = BS(result.content, "html.parser")
            categories = self.get_category()
            token = soup.select_one("meta[name=csrf-token]")["content"]
            headers = {
                "Accept": "text/javascript",
                "X-CSRF-Token": token,
                "X-Requested-With": "XMLHttpRequest",
            }
            date_str = date.strftime("%Y/%m/%d")
            accounts = self.get_account()
            post_data = {
                "user_asset_act[updated_at]": date_str,
                "user_asset_act[recurring_flag]": 0,
                "user_asset_act[amount]": abs(amount),
                "user_asset_act[content]": content,
                "commit": "保存する",
            }
            if is_transfer:
                ac_id_from = accounts[account[0]]["edit_id"]
                ac_id_to = accounts[account[1]]["edit_id"]
                post_data_add = {
                    "user_asset_act[is_transfer]": 1,
                    "user_asset_act[sub_account_id_hash_from]": ac_id_from,
                    "user_asset_act[sub_account_id_hash_to]": ac_id_to,
                }
                post_data.update(post_data_add)
            else:
                if amount > 0:
                    is_income = 1
                    l_c_id = categories["plus"][lcategory]["id"]
                    m_c_id = categories["plus"][lcategory][mcategory]["id"]
                else:
                    is_income = 0
                    l_c_id = categories["minus"][lcategory]["id"]
                    m_c_id = categories["minus"][lcategory][mcategory]["id"]
                ac_id = accounts[account]["edit_id"]
                post_data_add = {
                    "user_asset_act[is_transfer]": 0,
                    "user_asset_act[is_income]": is_income,
                    "user_asset_act[sub_account_id_hash]": ac_id,
                    "user_asset_act[large_category_id]": l_c_id,
                    "user_asset_act[middle_category_id]": m_c_id,
                }
                post_data.update(post_data_add)
            self._session.post(
                "https://moneyforward.com/cf/create",
                data=post_data,
                headers=headers,
                timeout=self._timeout,
            ).raise_for_status()
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)

    def update(
        self,
        transaction_id,
        amount,
        date=None,
        content=None,
        account=None,
        lcategory=None,
        mcategory=None,
        memo=None,
    ):
        try:
            result = self._session.get("https://moneyforward.com/cf", timeout=self._timeout)
            result.raise_for_status()
            soup = BS(result.content, "html.parser")
            categories = self.get_category()
            token = soup.select_one("meta[name=csrf-token]")["content"]
            headers = {
                "Accept": "text/javascript",
                "X-CSRF-Token": token,
                "X-Requested-With": "XMLHttpRequest",
            }
            accounts = self.get_account()
            put_data = {
                "user_asset_act[id]": transaction_id,
                "user_asset_act[table_name]": "user_asset_act",
            }
            if date is not None:
                date_str = date.strftime("%Y/%m/%d")
                put_data.update({"user_asset_act[updated_at]": date_str})
            if amount is not None:
                put_data.update({"user_asset_act[amount]": amount})
            if content is not None:
                put_data.update({"user_asset_act[content]": content})
            if memo is not None:
                put_data.update({"user_asset_act[memo]": memo})
            if amount > 0:
                is_income = 1
            else:
                is_income = 0
            put_data.update({"user_asset_act[is_income]": is_income})
            if lcategory is not None and mcategory is not None:
                if amount > 0:
                    l_c_id = categories["plus"][lcategory]["id"]
                    m_c_id = categories["plus"][lcategory][mcategory]["id"]
                else:
                    l_c_id = categories["minus"][lcategory]["id"]
                    m_c_id = categories["minus"][lcategory][mcategory]["id"]
                put_data.update({"user_asset_act[large_category_id]": l_c_id})
                put_data.update({"user_asset_act[middle_category_id]": m_c_id})
            if account is not None:
                ac_id = accounts[account]["edit_id"]
                put_data.update({"user_asset_act[sub_account_id_hash]": ac_id})
            self._session.put(
                "https://moneyforward.com/cf/update",
                params=put_data,
                headers=headers,
                timeout=self._timeout,
            ).raise_for_status()
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)

    def transfer(self, transaction_id, partner_account, partner_sub_account=None, partner_id=None):
        try:
            result = self._session.get("https://moneyforward.com/cf", timeout=self._timeout)
            result.raise_for_status()
            soup = BS(result.content, "html.parser")
            token = soup.select_one("meta[name=csrf-token]")["content"]
            headers = {
                "Accept": "text/javascript",
                "X-CSRF-Token": token,
                "X-Requested-With": "XMLHttpRequest",
            }
            self._session.put(
                "https://moneyforward.com/cf/update.js",
                params={"change_type": "enable_transfer", "id": transaction_id},
                headers=headers,
                timeout=self._timeout,
            ).raise_for_status()
            result = self._session.post(
                "https://moneyforward.com/cf/fetch_transfer",
                data={"user_asset_act_id": transaction_id},
                headers=headers,
                timeout=self._timeout,
            )
            result.raise_for_status()
            search_result = re.search(r"\.html\((.*?)\);", result.text)
            html = search_result.group(1)
            html = eval(html).replace("\\", "")
            soup = BS(html, "html.parser")
            options = soup.select("option")
            ac_list = {}
            for option in options:
                ac_list.update({option.text: option["value"]})
            ac_id = ac_list[partner_account]
            result = self._session.post(
                "https://moneyforward.com/cf/fetch_transfer",
                data={"user_asset_act_id": transaction_id, "account_id_hash": ac_id},
                headers=headers,
                timeout=self._timeout,
            )
            result.raise_for_status()
            search_result = re.search(r"\.html\((.*?)\);", result.text)
            html = search_result.group(1)
            html = eval(html).replace("\\", "")
            soup = BS(html, "html.parser")
            tmp = soup.select_one("#user_asset_act_partner_sub_account_id_hash")
            if tmp.has_attr("value"):
                sub_ac_id = tmp["value"]
            else:
                options = soup.select("option")
                sub_ac_list = {}
                for option in options:
                    sub_ac_list.update({option.text: option["value"]})
                sub_ac_id = sub_ac_list[partner_sub_account]
            post_data = {
                "_method": "put",
                "user_asset_act[id]": transaction_id,
                "user_asset_act[partner_account_id_hash]": ac_id,
                "user_asset_act[partner_sub_account_id_hash]": sub_ac_id,
                "commit": "設定を保存",
            }
            if partner_id is not None:
                post_data.update({"user_asset_act[partner_act_id]": partner_id})
            self._session.post(
                "https://moneyforward.com/cf/update",
                data=post_data,
                headers=headers,
                timeout=self._timeout,
            ).raise_for_status()
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)

    def disable_transfer(self, transaction_id):
        try:
            result = self._session.get("https://moneyforward.com/cf", timeout=self._timeout)
            result.raise_for_status()
            soup = BS(result.content, "html.parser")
            token = soup.select_one("meta[name=csrf-token]")["content"]
            headers = {
                "Accept": "text/javascript",
                "X-CSRF-Token": token,
                "X-Requested-With": "XMLHttpRequest",
            }
            self._session.put(
                "https://moneyforward.com/cf/update.js",
                params={"change_type": "disable_transfer", "id": transaction_id},
                headers=headers,
                timeout=self._timeout,
            ).raise_for_status()
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)

    def delete(self, transaction_id):
        try:
            result = self._session.get("https://moneyforward.com/cf", timeout=self._timeout)
            result.raise_for_status()
            soup = BS(result.content, "html.parser")
            token = soup.select_one("meta[name=csrf-token]")["content"]
            headers = {
                "Accept": "text/javascript",
                "X-CSRF-Token": token,
                "X-Requested-With": "XMLHttpRequest",
            }
            self._session.delete(
                "https://moneyforward.com/cf/" + str(transaction_id),
                headers=headers,
                timeout=self._timeout,
            ).raise_for_status()
        except (Timeout, HTTPError) as e:
            raise MFConnectionError(e)
