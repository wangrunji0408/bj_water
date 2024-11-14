import asyncio
import json
from datetime import datetime
from .const import LOGGER
# import logging
# LOGGER = logging.getLogger(__package__)

SERVICE_HOST = "https://www.bjwatergroupkf.com.cn"


class InvalidData(Exception):
    pass


class BJWater:
    def __init__(self, session, user_code: str) -> None:
        self._session = session
        self.user_code = user_code
        self.bill_cycle = []
        self.info = {"cycle": {}, "user_code": user_code}

    async def get_bill_cycle_range(self, user_code: str) -> list:
        """
        获取账单周期
        :return: ["2023-08", "2022-02"]
        """
        LOGGER.info("get_bill_cycle_range user code: " + str(user_code))
        bill_month_api = SERVICE_HOST + "/api/member/bizMyWater/getPcMonthsAndYears"
        response = await self._session.get(
            url=bill_month_api, params={"userCode": user_code}
        )
        if response.status != 200:
            LOGGER.error(f"get_monthly_bill res state code: {response.status}")
            raise InvalidData(f"get_bill_month_range response status_code: {response.status}")
        json_body = json.loads(await response.read())
        # json_body = {'msg': '操作成功', 'code': 0, 'data': {'months': ['2024年11月', '2024年09月'], 'years': [2024, 2023]}}
        LOGGER.info("get_bill_cycle_range response: " + str(json_body))
        data = json_body["data"]
        if not ("months" in data.keys() and len(data["months"]) > 0):
            raise InvalidData(f"未查到账单周期,请检查户号: {user_code}!")
        cycle_date = sorted([datetime.strptime(date, "%Y年%m月").date().strftime("%Y-%m") for date in data["months"]])
        LOGGER.info("get_bill_cycle_range end " + str(cycle_date))
        return cycle_date

    async def get_payment_bill(self):
        """
        获取缴费账单
        amount: 当前周期总费用
        date: 缴费时间
        szyf: 水资源费改税
        wsf: 污水处理费
        sf: 水费
        :return:
        """
        response = await self._session.get(
            url=SERVICE_HOST + "/api/member/bizMyWater/pcPaymentRecord",
            params={"userCode": self.user_code},
            timeout=10
        )
        if response.status != 200:
            LOGGER.error("get_payment_bill res state code: %s" % (response.status))
            raise InvalidData(f"get_payment_bill response status_code = {response.status}")
        json_body = json.loads(await response.read())
        LOGGER.info("get_payment_bill: " + str(json_body))
        bill_list = json_body["data"]
        if len(bill_list) == 0:
            raise InvalidData("未查询到缴费记录,请检查水表户号!")
        index = 0
        for bill in bill_list:
            cycle_date = (datetime.strptime(bill["billDate"], "%Y年%m月").date().strftime("%Y-%m"))
            if cycle_date in self.bill_cycle:
                amount_detail = {
                    "index": index,
                    "fee": {
                        "pay": 1,
                        "date": datetime.strptime(bill["date"], "%Y.%m.%d").date().strftime("%Y-%m-%d"),
                        "amount": bill["amount"],
                        "szyf": bill["szyf"],
                        "wsf": bill["wsf"],
                        "sf": bill["sf"],
                    }
                }
                self.info["cycle"].update({cycle_date: amount_detail})
            index += 1
        LOGGER.info("get_payment_bill end " + str(self.info))

    async def get_monthly_bill(self, bill_cycle: str):
        """
        获取单个月份的账单详情
        :param bill_cycle: 账单周期 如 2023年6月
        :return:
        """
        monthly_api = SERVICE_HOST + "/api/member/bizMyWater/getPcMonthlyBill"
        params = {"userCode": self.user_code, "billDate": bill_cycle}
        response = await self._session.get(url=monthly_api, params=params, timeout=10)
        if response.status == 200:
            json_body = json.loads(await response.read())
            LOGGER.info("get_monthly_bill: " + str(json_body))
            detail_data = json_body["data"]
            if detail_data["endValue"] == "":
                raise InvalidData("未查询到账单详情,请检查账单周期是否错误!")

            if bill_cycle not in self.info["cycle"].keys():
                amount_detail = {
                    "index": 0,
                    "fee": {
                        "pay": 0,
                        "date": bill_cycle,
                        "amount": detail_data["amount"],
                        "sf": detail_data["firstStep"]["amount"],       # 水费
                        "szyf": detail_data["taxFee"]["amount"],        # 水资源费
                        "wsf": detail_data["waterborneFee"]["amount"],  # 污水处理费
                    },
                    "meter": {
                        "usage": detail_data["total"],
                        "value": meter_value_to_int(detail_data["endValue"]),
                    },
                }
                self.info["cycle"].update({bill_cycle: amount_detail})

            self.info["cycle"][bill_cycle].update(
                {
                    "meter": {
                        "usage": detail_data["total"],
                        "value": meter_value_to_int(detail_data["endValue"]),
                    }
                }
            )
            if "total_usage" not in self.info.keys() or self.info["total_usage"] < int(detail_data["grandTotal"]):
                self.info.update({"total_usage": int(detail_data["grandTotal"])})  # 记录第一阶梯总使用量
            self.info.update({"meter_value": meter_value_to_int(detail_data["endValue"])})
            self.info.update({"first_step_price": float(detail_data["firstStep"]["price"])})  # 记录第一阶梯水费单价
            self.info.update({"wastwater_treatment_price": float(detail_data["waterborneFee"]["price"])})  # 污水处理费
            self.info.update({"water_tax": float(detail_data["taxFee"]["price"])})  # 水资源费
            self.info.update({"second_step_left": int(detail_data["stepLeft"]["second"])})  # 记录第二阶梯剩余使用量
            self.info.update({"total_cost": self.info["water_tax"] + self.info["first_step_price"] + self.info["wastwater_treatment_price"]})
            self.info.update({"total_amount": detail_data["amount"]})
            self.info.update({"last_period": bill_cycle})
        else:
            LOGGER.error("get_monthly_bill res state code: %s" % (response.status))
            raise InvalidData(f"get_monthly_bill response status_code = {response.status}")

    async def fetch_data(self):
        self.bill_cycle = await self.get_bill_cycle_range(self.user_code)
        await self.get_payment_bill()
        for bill_cycle in self.bill_cycle:
            await self.get_monthly_bill(bill_cycle)
        return self.info

def meter_value_to_int(meter_value: str) -> int:
    a, b = meter_value.split("/")
    return int(a) * 1000 + int(b)


# import aiohttp
# import pprint

# async def main():
#     async with aiohttp.ClientSession() as session:
#         bj_water = BJWater(session, "")
#         await bj_water.fetch_data()
#         pprint.pprint(bj_water.info)

# if __name__ == "__main__":
#     import logging
#     logging.basicConfig(level=logging.INFO)
#     asyncio.run(main())
