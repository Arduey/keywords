-- MySQL 表结构 —— 关键词排名管理系统
--
-- 这份文件由 Web 安装向导 (/install) 自动执行，一般不需要手动导入。
-- 如需手动导入，可在宝塔面板 → 数据库 → 管理(phpMyAdmin) → 导入 中选择本文件。

CREATE TABLE IF NOT EXISTS `products` (
  `asin`       VARCHAR(20)  NOT NULL COMMENT '亚马逊 ASIN',
  `name`       VARCHAR(255) NOT NULL COMMENT '产品名称',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`asin`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `rankings` (
  `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `asin`         VARCHAR(20)     NOT NULL,
  `date`         DATE            NOT NULL COMMENT '采集日期',
  `rating`       DECIMAL(3,1)    NOT NULL DEFAULT 4.5,
  `review_count` INT             NOT NULL DEFAULT 92,
  `rank`         VARCHAR(1000)   NOT NULL DEFAULT '' COMMENT '大类/小类排名，可多行',
  `keyword`      VARCHAR(191)    NOT NULL,
  `natural_pos`  VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '自然位含广告',
  `ad_pos`       VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '广告位含自然',
  `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_rankings_asin_date_keyword` (`asin`, `date`, `keyword`),
  KEY `idx_rankings_asin` (`asin`),
  KEY `idx_rankings_date` (`date`),
  CONSTRAINT `fk_rankings_product` FOREIGN KEY (`asin`) REFERENCES `products` (`asin`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 系统设置表：目前用于存放站点访问密钥 (site_password)。
-- 访问密钥由安装向导写入，因此这里不预置默认值，避免留下可猜测的弱口令。
CREATE TABLE IF NOT EXISTS `settings` (
  `key`   VARCHAR(64)  NOT NULL,
  `value` TEXT         NOT NULL,
  PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
