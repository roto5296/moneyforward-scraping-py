import datetime
import re
import time
import urllib

import requests
from bs4 import BeautifulSoup as BS


class MFScraper:
    def __init__(self, id, passwd):
        self._id = id
        self._passwd = passwd
        self._session = requests.session()

    def login(self):
        result = self._session.get("https://moneyforward.com/sign_in/")
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
        result = self._session.post("https://id.moneyforward.com/sign_in", data=post_data)
        if result.url == "https://moneyforward.com/" and result.status_code == 200:
            return True
        else:
            return False

    def fetch(self, delay=2, maxwaiting=300):
        result = self._session.get("https://moneyforward.com")
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
            self._session.post("https://moneyforward.com" + url, headers=headers)
        counter = 0
        while counter < maxwaiting:
            time.sleep(delay)
            counter += delay
            result = self._session.get("https://moneyforward.com/accounts/polling")
            if not result.json()["loading"]:
                return True
        return False

    def get(self, year, month):
        result = self._session.get("https://moneyforward.com")
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
            "https://moneyforward.com/cf/fetch", data=post_data, headers=headers
        )
        html = re.search(r'\$\("\.list_body"\)\.append\((.*?)\);', result.text).group(1)
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
                account_to = to.text.replace("\n", "")
                account_from = td_calc.text.replace("\n", "")
            elif amount > 0:
                account_to = td_calc.text.replace("\n", "")
                account_from = None
            else:
                account_to = None
                account_from = td_calc.text.replace("\n", "")
            transaction = {
                "transaction_id": transaction_id,
                "date": date,
                "amount": abs(amount),
                "account_from": account_from,
                "account_to": account_to,
                "lcategory": tr.select_one("td.lctg").text.replace("\n", ""),
                "mcategory": tr.select_one("td.mctg").text.replace("\n", ""),
                "content": tr.select_one("td.content").text.replace("\n", ""),
                "memo": tr.select_one("td.memo").text.replace("\n", ""),
            }
            ret.append(transaction)
        ret = sorted(ret, key=lambda x: (x["date"], x["transaction_id"]), reverse=True)
        return ret

    def get_account(self):
        result = self._session.get("https://moneyforward.com")
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
        return accounts

    def get_category(self):
        result = self._session.get("https://moneyforward.com/cf")
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
        price,
        account,
        l_category="未分類",
        m_category="未分類",
        memo="",
        is_transfer=False,
    ):
        result = self._session.get("https://moneyforward.com/cf")
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
            "user_asset_act[amount]": abs(price),
            "user_asset_act[content]": memo,
            "commit": "保存する",
        }
        try:
            if is_transfer:
                ac_id_from = accounts[account[0]]["moneyforward_id"]
                ac_id_to = accounts[account[1]]["moneyforward_id"]
                post_data_add = {
                    "user_asset_act[is_transfer]": 1,
                    "user_asset_act[sub_account_id_hash_from]": ac_id_from,
                    "user_asset_act[sub_account_id_hash_to]": ac_id_to,
                }
                post_data.update(post_data_add)
            else:
                if price > 0:
                    is_income = 1
                    l_c_id = categories["plus"][l_category]["id"]
                    m_c_id = categories["plus"][l_category][m_category]["id"]
                else:
                    is_income = 0
                    l_c_id = categories["minus"][l_category]["id"]
                    m_c_id = categories["minus"][l_category][m_category]["id"]
                ac_id = accounts[account]["moneyforward_id"]
                post_data_add = {
                    "user_asset_act[is_transfer]": 0,
                    "user_asset_act[is_income]": is_income,
                    "user_asset_act[sub_account_id_hash]": ac_id,
                    "user_asset_act[large_category_id]": l_c_id,
                    "user_asset_act[middle_category_id]": m_c_id,
                }
                post_data.update(post_data_add)
        except BaseException:
            return False
        result = self._session.post(
            "https://moneyforward.com/cf/create", data=post_data, headers=headers
        )
        return True
