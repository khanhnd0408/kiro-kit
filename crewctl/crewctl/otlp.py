"""OTLP/HTTP protobuf encoding with the standard library only.

Some backends (VictoriaMetrics, for one) accept OTLP only as binary protobuf, so this module writes the wire format by
hand instead of OTLP/JSON. It covers exactly what `crewctl usage` sends: log records, and monotonic sums and gauges with
number data points. Field numbers follow opentelemetry-proto v1 (common, resource, logs and metrics .proto files).
"""

import struct

# Wire types.
_VARINT, _I64, _LEN = 0, 1, 2

# AggregationTemporality.
CUMULATIVE = 2

# SeverityNumber.
SEVERITY_INFO = 9


def _varint(value):
    value &= (1 << 64) - 1          # negative int64 values are sent as their two's complement
    out = bytearray()
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            return bytes(out)


def _key(field, wire):
    return _varint((field << 3) | wire)


def _len(field, payload):
    return _key(field, _LEN) + _varint(len(payload)) + payload


def _str(field, text):
    return _len(field, text.encode("utf-8"))


def _fixed64(field, value):
    return _key(field, _I64) + struct.pack("<Q", value)


def _double(field, value):
    return _key(field, _I64) + struct.pack("<d", value)


def _uint(field, value):
    return _key(field, _VARINT) + _varint(value)


def any_value(value):
    """AnyValue: string 1, bool 2, int 3, double 4. A oneof member is always written, even when it is zero."""
    if isinstance(value, bool):
        return _uint(2, 1 if value else 0)
    if isinstance(value, int):
        return _uint(3, value)
    if isinstance(value, float):
        return _double(4, value)
    return _str(1, str(value))


def key_value(key, value):
    return _str(1, key) + _len(2, any_value(value))


def _attributes(field, attrs):
    return b"".join(_len(field, key_value(k, v)) for k, v in attrs.items())


def _resource(attrs):
    return _attributes(1, attrs)


def _scope(name, version):
    return _str(1, name) + (_str(2, version) if version else b"")


def log_record(time_ns, body, attrs, observed_ns=0):
    """LogRecord: time 1, severity_number 2, severity_text 3, body 5, attributes 6, observed_time 11."""
    out = _fixed64(1, time_ns) + _uint(2, SEVERITY_INFO) + _str(3, "INFO") + _len(5, any_value(body)) + _attributes(6, attrs)
    if observed_ns:
        out += _fixed64(11, observed_ns)
    return out


def logs_request(resource_attrs, scope, records):
    """ExportLogsServiceRequest > ResourceLogs > ScopeLogs > LogRecord. `scope` is (name, version)."""
    scope_logs = _len(1, _scope(*scope)) + b"".join(_len(2, r) for r in records)
    resource_logs = _len(1, _resource(resource_attrs)) + _len(2, scope_logs)
    return _len(1, resource_logs)


def number_point(attrs, start_ns, time_ns, value):
    """NumberDataPoint: start 2, time 3, as_double 4 or as_int 6 (sfixed64), attributes 7."""
    out = _fixed64(2, start_ns) if start_ns else b""
    out += _fixed64(3, time_ns)
    if isinstance(value, int) and not isinstance(value, bool):
        out += _key(6, _I64) + struct.pack("<q", value)
    else:
        out += _double(4, float(value))
    return out + _attributes(7, attrs)


def sum_metric(name, description, unit, points):
    """Metric with a monotonic cumulative Sum (field 7): points 1, temporality 2, is_monotonic 3."""
    body = b"".join(_len(1, p) for p in points) + _uint(2, CUMULATIVE) + _uint(3, 1)
    return _metric_head(name, description, unit) + _len(7, body)


def gauge_metric(name, description, unit, points):
    """Metric with a Gauge (field 5): points 1."""
    return _metric_head(name, description, unit) + _len(5, b"".join(_len(1, p) for p in points))


def _metric_head(name, description, unit):
    return _str(1, name) + (_str(2, description) if description else b"") + (_str(3, unit) if unit else b"")


def metrics_request(resource_attrs, scope, metrics):
    """ExportMetricsServiceRequest > ResourceMetrics > ScopeMetrics > Metric."""
    scope_metrics = _len(1, _scope(*scope)) + b"".join(_len(2, m) for m in metrics)
    resource_metrics = _len(1, _resource(resource_attrs)) + _len(2, scope_metrics)
    return _len(1, resource_metrics)


# ---- decoding, for --dry-run and the tests: turns our own bytes back into plain data -------------------------------

def _read_varint(data, pos):
    shift = result = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def fields(data):
    """Split one message into (field, wire, raw) tuples. raw is an int for varints, 8 bytes for I64, bytes for LEN."""
    pos, out = 0, []
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field, wire = key >> 3, key & 7
        if wire == _VARINT:
            value, pos = _read_varint(data, pos)
        elif wire == _I64:
            value, pos = data[pos:pos + 8], pos + 8
        elif wire == _LEN:
            size, pos = _read_varint(data, pos)
            value, pos = data[pos:pos + size], pos + size
        elif wire == 5:
            value, pos = data[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
        out.append((field, wire, value))
    return out


def decode_any(data):
    for field, _, raw in fields(data):
        if field == 1:
            return raw.decode("utf-8")
        if field == 2:
            return bool(raw)
        if field == 3:
            return raw - (1 << 64) if raw >= 1 << 63 else raw
        if field == 4:
            return struct.unpack("<d", raw)[0]
    return None


def decode_attrs(items):
    out = {}
    for raw in items:
        parts = {f: v for f, _, v in fields(raw)}
        out[parts[1].decode("utf-8")] = decode_any(parts.get(2, b""))
    return out


def decode_logs(data):
    """ExportLogsServiceRequest bytes -> [{"resource": {...}, "time_ns": n, "body": ..., "attributes": {...}}]."""
    found = []
    for _, _, resource_logs in fields(data):
        parts = fields(resource_logs)
        resource = next((decode_attrs([v for f, _, v in fields(raw) if f == 1]) for f, _, raw in parts if f == 1), {})
        for f, _, scope_logs in parts:
            if f != 2:
                continue
            for g, _, record in fields(scope_logs):
                if g != 2:
                    continue
                rec = fields(record)
                found.append({
                    "resource": resource,
                    "time_ns": next(struct.unpack("<Q", v)[0] for h, _, v in rec if h == 1),
                    "body": next((decode_any(v) for h, _, v in rec if h == 5), None),
                    "attributes": decode_attrs([v for h, _, v in rec if h == 6]),
                })
    return found


def decode_metrics(data):
    """ExportMetricsServiceRequest bytes -> [{"name", "kind", "points": [{"attributes", "value", "time_ns", "start_ns"}]}]."""
    found = []
    for _, _, resource_metrics in fields(data):
        for f, _, scope_metrics in fields(resource_metrics):
            if f != 2:
                continue
            for g, _, metric in fields(scope_metrics):
                if g != 2:
                    continue
                parts = fields(metric)
                name = next(v.decode("utf-8") for h, _, v in parts if h == 1)
                for h, _, body in parts:
                    if h not in (5, 7):
                        continue
                    points = []
                    for i, _, point in fields(body):
                        if i != 1:
                            continue
                        p = {"attributes": {}, "start_ns": 0}
                        attrs = []
                        for j, _, v in fields(point):
                            if j == 2:
                                p["start_ns"] = struct.unpack("<Q", v)[0]
                            elif j == 3:
                                p["time_ns"] = struct.unpack("<Q", v)[0]
                            elif j == 4:
                                p["value"] = struct.unpack("<d", v)[0]
                            elif j == 6:
                                p["value"] = struct.unpack("<q", v)[0]
                            elif j == 7:
                                attrs.append(v)
                        p["attributes"] = decode_attrs(attrs)
                        points.append(p)
                    found.append({"name": name, "kind": "sum" if h == 7 else "gauge", "points": points})
    return found
