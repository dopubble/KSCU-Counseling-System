from django.test import RequestFactory, TestCase

from apps.reports.table_sort import (
    SortFieldSpec,
    build_sort_query,
    parse_sort,
    sort_list,
    sort_queryset,
)
from apps.accounts.models import User, UserRole, UserStatus


class TableSortTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_parse_sort_defaults_invalid(self):
        request = self.factory.get("/admin/", {"sort": "bad", "dir": "sideways"})
        sort = parse_sort(
            request,
            allowed=("name",),
            default_field="name",
            default_direction="desc",
        )
        self.assertEqual(sort.field, "name")
        self.assertEqual(sort.direction, "desc")

    def test_build_sort_query_toggles_direction(self):
        request = self.factory.get("/admin/", {"tab": "active", "sort": "client", "dir": "asc"})
        href = build_sort_query(request, "client")
        self.assertIn("dir=desc", href)
        self.assertIn("sort=client", href)
        self.assertIn("tab=active", href)

    def test_sort_queryset_by_orm_field(self):
        User.objects.create_user(
            email="b@example.com",
            password="x",
            name="B",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        User.objects.create_user(
            email="a@example.com",
            password="x",
            name="A",
            role=UserRole.CLIENT,
            status=UserStatus.ACTIVE,
        )
        from apps.reports.table_sort import SortState

        qs = User.objects.filter(role=UserRole.CLIENT)
        sorted_qs = sort_queryset(
            qs,
            SortState("name", "asc"),
            (SortFieldSpec("name", orm="name"),),
        )
        names = list(sorted_qs.values_list("name", flat=True))
        self.assertEqual(names, ["A", "B"])

    def test_sort_list_python_key(self):
        rows = [{"label": "bbb"}, {"label": "aaa"}]
        from apps.reports.table_sort import SortState

        sorted_rows = sort_list(
            rows,
            SortState("label", "asc"),
            (SortFieldSpec("label", python_key=lambda row: row["label"]),),
        )
        self.assertEqual([row["label"] for row in sorted_rows], ["aaa", "bbb"])
