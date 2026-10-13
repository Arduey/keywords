# -*- coding: utf-8 -*-
"""Excel 2003 XML 电子表格（.xls）生成。

输出与 Cloudflare 版 functions/api/export.js 逐字节一致，
保证迁移后导出的表格和旧版没有任何差别。
"""
import datetime
import re

# Excel 日期序列号的起点（与原 JS 版 new Date('1899-12-30T00:00:00') 对应）
_DATE_BASE = datetime.date(1899, 12, 30)

# 工作表名非法字符，对应 JS 的 /[\\\/\*\?\[\]:]/g
_SHEET_NAME_ILLEGAL = re.compile(r"[\\/*?\[\]:]")
# 把 "空白 + #" 规范成换行 + "#"，对应 JS 的 /\s+#/g
_RANK_BREAK = re.compile(r"\s+#")


def sort_asins(asins, product_order=None):
    """按用户保存的产品顺序排列 ASIN，未列出的按字母序追加在后面。

    与关键词排序同一套规则：先取已保存顺序中仍然存在的项，
    再把剩余（新增或已删除后剩下的）按字母序接上，保证结果稳定且不丢产品。
    """
    remaining = set(asins)
    ordered = []
    if isinstance(product_order, (list, tuple)):
        for asin in product_order:
            if asin in remaining:
                ordered.append(asin)
                remaining.discard(asin)
    ordered.extend(sorted(remaining))
    return ordered


def xml_esc(value):
    """XML 转义，顺序与原 JS 版 xmlEsc() 一致。"""
    if not value:
        return ""
    text = value if isinstance(value, str) else str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def date_to_serial(iso):
    """'YYYY-MM-DD' -> Excel 日期序列号。"""
    return (datetime.date.fromisoformat(iso) - _DATE_BASE).days


def generate_xml(data, kw_order, product_order=None):
    """把 {asin: {name, dates: {date: {rating, reviewCount, rank, keywords}}}} 渲染成 Excel XML。

    product_order 是用户拖拽出来的产品顺序（ASIN 列表）；
    未列出的产品按 ASIN 升序追加在后面。
    """
    asins = sort_asins(data.keys(), product_order)

    all_dates = set()
    for asin in asins:
        all_dates.update(data[asin]["dates"].keys())
    sorted_dates = sorted(all_dates)

    extra_cols = 5
    total_cols = 1 + len(sorted_dates) + extra_cols

    sheets = ""
    for asin in asins:
        product = data[asin]

        safe_name = _SHEET_NAME_ILLEGAL.sub("-", product["name"] or asin)[:31]

        # 该产品出现过的全部关键词
        p_kw_set = set()
        for dd in product["dates"].values():
            p_kw_set.update(dd["keywords"].keys())

        # 自定义顺序：单产品导出时前端传的是 {natural, ad}，
        # 全量导出时传的是 {asin: {natural, ad}}，两种都要兼容。
        # 对应 JS 的 `kwOrder[asin] || kwOrder || {}`：JS 里空对象 {} 是「真值」，
        # 所以“存在但为空的对象”要原样采用，不能回退成整体顺序。
        candidate = kw_order.get(asin)
        if isinstance(candidate, dict):
            order = candidate
        else:
            order = kw_order if isinstance(kw_order, dict) else {}

        ordered_n, ordered_a = [], []
        remaining = set(p_kw_set)
        for kw in order.get("natural") or []:
            if kw in remaining:
                ordered_n.append(kw)
                remaining.discard(kw)
        for kw in order.get("ad") or []:
            if kw in remaining:
                ordered_a.append(kw)
                remaining.discard(kw)
        full_ordered = ordered_n + ordered_a + sorted(remaining)
        # 保序去重，对应 JS 的 [...new Set(fullOrdered)]
        p_kw_list = list(dict.fromkeys(full_ordered))

        sheets += '<Worksheet ss:Name="%s">\n<Table>\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n</Table>\n<x:WorksheetOptions>\n<FreezePanes/><FrozenNoSplit/>\n<SplitHorizontal>3</SplitHorizontal><TopRowBottomPane>3</TopRowBottomPane>\n<SplitVertical>1</SplitVertical><LeftColumnRightPane>1</LeftColumnRightPane>\n<x:ActiveCol>%d</x:ActiveCol><x:ActiveRow>0</x:ActiveRow>\n</x:WorksheetOptions>\n</Worksheet>\n' % (
            xml_esc(safe_name),
            _col_defs(total_cols),
            _header_row(sorted_dates, xml_esc(product["name"]), extra_cols),
            _rank_row(sorted_dates, product, xml_esc(asin), extra_cols),
            _rating_row(sorted_dates, product, extra_cols),
            _section_row("自然位-精准词", total_cols),
            _kw_rows(p_kw_list, sorted_dates, product, "n", extra_cols),
            _empty_row(total_cols),
            _section_row("广告位-精准词", total_cols),
            _kw_rows(p_kw_list, sorted_dates, product, "a", extra_cols),
            len(sorted_dates),
        )

    return '<?xml version="1.0" encoding="UTF-8"?>\n<?mso-application progid="Excel.Sheet"?>\n<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"\n xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"\n xmlns:x="urn:schemas-microsoft-com:office:excel">\n<Styles>\n<Style ss:ID="date"><Font ss:FontName="等线" ss:Size="11"/><NumberFormat ss:Format="M月d日"/><Alignment ss:Horizontal="Center" ss:Vertical="Center"/></Style>\n<Style ss:ID="center"><Font ss:FontName="等线" ss:Size="11"/><Alignment ss:Horizontal="Center" ss:Vertical="Center"/></Style>\n<Style ss:ID="left"><Font ss:FontName="等线" ss:Size="11"/><Alignment ss:Horizontal="Left" ss:Vertical="Center" ss:WrapText="1"/></Style>\n<Style ss:ID="rank"><Font ss:FontName="等线" ss:Size="8"/><Alignment ss:Horizontal="Left" ss:Vertical="Center" ss:WrapText="1"/></Style>\n<Style ss:ID="section"><Font ss:FontName="等线" ss:Size="11" ss:Color="#FFFFFF" ss:Bold="1"/><Alignment ss:Horizontal="Left" ss:Vertical="Center"/><Interior ss:Color="#5B9BD5" ss:Pattern="Solid"/></Style>\n</Styles>\n%s\n</Workbook>' % sheets


def _col_defs(n):
    return "".join('<Column ss:Width="120"/>\n' for _ in range(n))


def _header_row(dates, name, extra):
    s = '<Row ss:Height="20">'
    s += '<Cell ss:StyleID="center"><Data ss:Type="String">%s</Data></Cell>' % xml_esc(name)
    for d in dates:
        s += '<Cell ss:StyleID="date"><Data ss:Type="Number">%d</Data></Cell>' % date_to_serial(d)
    for _ in range(extra):
        s += '<Cell ss:StyleID="center"></Cell>'
    return s + "</Row>\n"


def _rank_row(dates, product, asin, extra):
    s = '<Row ss:Height="29">'
    s += '<Cell ss:StyleID="left"><Data ss:Type="String">%s</Data></Cell>' % xml_esc(asin)
    for d in dates:
        dd = product["dates"].get(d)
        rank = (dd["rank"] if dd else "") or ""
        rank = _RANK_BREAK.sub("\n#", xml_esc(rank)).strip()
        s += '<Cell ss:StyleID="rank"><Data ss:Type="String">%s</Data></Cell>' % rank
    for _ in range(extra):
        s += '<Cell ss:StyleID="left"></Cell>'
    return s + "</Row>\n"


def _rating_row(dates, product, extra):
    s = '<Row ss:Height="20">'
    s += '<Cell ss:StyleID="center"></Cell>'
    for d in dates:
        dd = product["dates"].get(d)
        cell = "%s - %s" % (dd["rating"], dd["reviewCount"]) if dd else ""
        s += '<Cell ss:StyleID="center"><Data ss:Type="String">%s</Data></Cell>' % cell
    for _ in range(extra):
        s += '<Cell ss:StyleID="center"></Cell>'
    return s + "</Row>\n"


def _section_row(title, cols):
    s = '<Row ss:Height="20">'
    s += '<Cell ss:StyleID="section"><Data ss:Type="String">%s</Data></Cell>' % xml_esc(title)
    for _ in range(1, cols):
        s += '<Cell ss:StyleID="section"></Cell>'
    return s + "</Row>\n"


def _kw_rows(kw_list, dates, product, kind, extra):
    s = ""
    for kw in kw_list:
        s += '<Row ss:Height="20">'
        s += '<Cell ss:StyleID="left"><Data ss:Type="String">%s</Data></Cell>' % xml_esc(kw)
        for d in dates:
            dd = product["dates"].get(d)
            pos = dd["keywords"][kw][kind] if (dd and kw in dd["keywords"]) else ""
            s += '<Cell ss:StyleID="center"><Data ss:Type="String">%s</Data></Cell>' % xml_esc(pos)
        for _ in range(extra):
            s += '<Cell ss:StyleID="center"></Cell>'
        s += "</Row>\n"
    return s


def _empty_row(cols):
    s = '<Row ss:Height="16">'
    for _ in range(cols):
        s += "<Cell></Cell>"
    return s + "</Row>\n"
