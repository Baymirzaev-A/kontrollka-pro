CREATE DATABASE IF NOT EXISTS kontrollka_metrics;

-- Таблица для снапшотов устройств
CREATE TABLE IF NOT EXISTS kontrollka_metrics.device_snapshots (
    ip String,
    name String,
    device_type String,
    vendor String,
    firmware String,
    serial String,
    location String,
    contact String,
    config String,
    interfaces_count UInt32,
    last_collected DateTime
) ENGINE = MergeTree()
ORDER BY last_collected;

-- Таблица для истории интерфейсов
CREATE TABLE IF NOT EXISTS kontrollka_metrics.interface_history (
    device_ip String,
    interface_name String,
    interface_index UInt32,
    interface_type String,
    speed UInt64,
    admin_status String,
    oper_status String,
    collected_at DateTime
) ENGINE = MergeTree()
ORDER BY collected_at;