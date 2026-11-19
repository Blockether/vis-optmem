"""A shared memory in Amazon DynamoDB: a ``Table`` for ``SharedStore`` with boto3.

Use it in Vis with:

    MEMORY_STORE=examples/dynamodb_store.py:dynamodb_store
    MEMORY_STORE_CONFIG={"table": "team-memory", "memory": "team", "region": "eu-west-1"}

One DynamoDB table holds many memories. The partition key ``pk`` is the name of a
memory, and the sort key ``sk`` is the key that ``SharedStore`` gives. ``create_table``
makes such a table. Each read is a consistent read, and each write is one
``TransactWriteItems`` call with ``attribute_not_exists`` and equality conditions.
boto3 is not a dependency of vis-optmem: install it where this store runs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from vis_optmem.shared import Absent, Condition, Equals, Item, SharedStore, Table

PARTITION = "pk"
SORT = "sk"
LAST = "\uffff"


def create_table(name: str, client=None) -> None:
    """Make a DynamoDB table for shared memories, billed for each request."""
    client = client or boto3.client("dynamodb")
    client.create_table(
        TableName=name,
        KeySchema=[
            {"AttributeName": PARTITION, "KeyType": "HASH"},
            {"AttributeName": SORT, "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": PARTITION, "AttributeType": "S"},
            {"AttributeName": SORT, "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=name)


class DynamoTable(Table):
    """The items of one memory: one partition of a DynamoDB table."""

    max_writes = 100
    """DynamoDB takes at most 100 actions in one transaction."""

    def __init__(self, table: str, memory: str = "team", client=None) -> None:
        self.table = table
        self.memory = memory
        self.client = client or boto3.client("dynamodb")
        self.location = f"dynamodb:{table}/{memory}"
        self._to = TypeSerializer()
        self._from = TypeDeserializer()

    def _key(self, key: str) -> dict:
        return {PARTITION: {"S": self.memory}, SORT: {"S": key}}

    def _item(self, raw: dict) -> Item:
        item = {
            name: self._from.deserialize(value)
            for name, value in raw.items()
            if name not in (PARTITION, SORT)
        }
        # DynamoDB gives numbers as Decimal. A memory keeps only whole numbers.
        return {
            name: int(value) if isinstance(value, Decimal) else value
            for name, value in item.items()
        }

    def get(self, key: str) -> Item | None:
        raw = self.client.get_item(
            TableName=self.table, Key=self._key(key), ConsistentRead=True
        ).get("Item")
        return None if raw is None else self._item(raw)

    def query(
        self, prefix: str, start: str = "", limit: int | None = None
    ) -> list[tuple[str, Item]]:
        found: list[tuple[str, Item]] = []
        request = {
            "TableName": self.table,
            "ConsistentRead": True,
            "KeyConditionExpression": "#p = :p AND #s BETWEEN :lo AND :hi",
            "ExpressionAttributeNames": {"#p": PARTITION, "#s": SORT},
            "ExpressionAttributeValues": {
                ":p": {"S": self.memory},
                ":lo": {"S": max(start, prefix)},
                ":hi": {"S": prefix + LAST},
            },
        }
        while limit is None or len(found) < limit:
            if limit is not None:
                request["Limit"] = limit - len(found)
            page = self.client.query(**request)
            found += [(raw[SORT]["S"], self._item(raw)) for raw in page["Items"]]
            if "LastEvaluatedKey" not in page:
                break
            request["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return found

    def _condition(self, condition: Condition) -> dict:
        """A condition expression for an action on the item of the condition."""
        if isinstance(condition, Absent):
            return {
                "ConditionExpression": "attribute_not_exists(#s)",
                "ExpressionAttributeNames": {"#s": SORT},
            }
        assert isinstance(condition, Equals)
        return {
            "ConditionExpression": "#f = :v",
            "ExpressionAttributeNames": {"#f": condition.field},
            "ExpressionAttributeValues": {":v": self._to.serialize(condition.value)},
        }

    def transact(
        self,
        puts: Mapping[str, Item] | None = None,
        deletes: Iterable[str] = (),
        conditions: Iterable[Condition] = (),
    ) -> bool:
        """One ``TransactWriteItems`` call. A condition joins the action on its item.

        DynamoDB takes one action for each item, so a condition on an item that
        the call does not write becomes a ``ConditionCheck``.
        """
        puts, deletes = dict(puts or {}), list(deletes)
        checks = {condition.key: condition for condition in conditions}
        actions = []
        for key, item in puts.items():
            put = {
                "TableName": self.table,
                "Item": self._key(key)
                | {name: self._to.serialize(value) for name, value in item.items()},
            }
            if key in checks:
                put |= self._condition(checks.pop(key))
            actions.append({"Put": put})
        for key in deletes:
            delete = {"TableName": self.table, "Key": self._key(key)}
            if key in checks:
                delete |= self._condition(checks.pop(key))
            actions.append({"Delete": delete})
        for key, condition in checks.items():
            check = {"TableName": self.table, "Key": self._key(key)}
            actions.append({"ConditionCheck": check | self._condition(condition)})
        if not actions:
            return True
        try:
            self.client.transact_write_items(TransactItems=actions)
        except self.client.exceptions.TransactionCanceledException as error:
            reasons = error.response.get("CancellationReasons", [])
            codes = {reason.get("Code", "None") for reason in reasons} - {"None"}
            # A false condition or a concurrent transaction: the caller tries again.
            if codes <= {"ConditionalCheckFailed", "TransactionConflict"}:
                return False
            raise
        return True


def dynamodb_store(
    table: str,
    memory: str = "team",
    region: str | None = None,
    endpoint_url: str | None = None,
    snapshot: str | None = None,
) -> SharedStore:
    """A ``SharedStore`` in the DynamoDB table ``table``, for ``MEMORY_STORE``."""
    client = boto3.client("dynamodb", region_name=region, endpoint_url=endpoint_url)
    return SharedStore(DynamoTable(table, memory, client), snapshot=snapshot)
