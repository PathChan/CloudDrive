CREATE DATABASE IF NOT EXISTS LiteDoc DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE LiteDoc;

-- 用户表
CREATE TABLE IF NOT EXISTS user (
    id       BIGINT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50)  NOT NULL UNIQUE,
    password VARCHAR(128) NOT NULL,
    email    VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 文件夹表
CREATE TABLE IF NOT EXISTS folder (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id     BIGINT       NOT NULL COMMENT '创建者',
    parent_id   BIGINT       DEFAULT 0 COMMENT '父文件夹ID',
    name        VARCHAR(255) NOT NULL COMMENT '文件夹名称',
    path_node   VARCHAR(500) DEFAULT '' COMMENT '路径节点，用于快速查询祖先',
    level       INT          DEFAULT 1 COMMENT '层级',
    is_deleted  TINYINT(1)   DEFAULT 0 COMMENT '是否删除',
    create_time DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_parent (parent_id),
    INDEX idx_user (user_id),
    INDEX idx_deleted (is_deleted),
    INDEX idx_path_node (path_node(100))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件夹表';

-- 文件表
CREATE TABLE IF NOT EXISTS file (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id          BIGINT       NOT NULL COMMENT '上传者',
    folder_id        BIGINT       DEFAULT 0 COMMENT '所属文件夹',
    name             VARCHAR(255) NOT NULL COMMENT '文件名',
    extension        VARCHAR(50)  DEFAULT '' COMMENT '扩展名',
    size             BIGINT       DEFAULT 0 COMMENT '文件大小',
    sha256           VARCHAR(64)  DEFAULT '' COMMENT '文件哈希',
    minio_bucket     VARCHAR(100) DEFAULT '' COMMENT 'MinIO桶名',
    minio_object_name VARCHAR(500) DEFAULT '' COMMENT 'MinIO对象名',
    mime_type        VARCHAR(127) DEFAULT NULL COMMENT 'MIME类型',
    pdf_object_name  VARCHAR(255) DEFAULT NULL COMMENT 'PDF预览版在MinIO中的对象键',
    is_deleted       TINYINT(1)   DEFAULT 0 COMMENT '是否删除',
    deleted_at       DATETIME     DEFAULT NULL COMMENT '删除时间',
    deleted_by       BIGINT       DEFAULT NULL COMMENT '删除者user_id',
    create_time      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    update_time      DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_folder (folder_id),
    INDEX idx_user (user_id),
    INDEX idx_deleted (is_deleted),
    INDEX idx_name_search (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文件表';

-- 用户收藏表
CREATE TABLE IF NOT EXISTS favorites (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id    BIGINT       NOT NULL,
    file_id    BIGINT       NOT NULL,
    item_type  VARCHAR(10)  NOT NULL DEFAULT 'file' COMMENT 'file or folder',
    created_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_file (user_id, file_id, item_type),
    INDEX idx_user (user_id),
    INDEX idx_file (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 删除记录表
CREATE TABLE IF NOT EXISTS delete_logs (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    file_id    BIGINT       NOT NULL,
    deleted_by BIGINT       NOT NULL COMMENT '谁删除的',
    deleted_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_file (file_id),
    INDEX idx_deleted_by (deleted_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 快捷访问表
CREATE TABLE IF NOT EXISTS quick_access (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id    BIGINT       NOT NULL,
    name       VARCHAR(255) NOT NULL,
    file_id    BIGINT       NOT NULL COMMENT '引用的文件夹或文件ID',
    is_folder  TINYINT(1)   DEFAULT 1 COMMENT '引用的是否为文件夹',
    sort_order INT          DEFAULT 0,
    created_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_file (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 初始化管理员用户
INSERT INTO user (username, password, email) VALUES ('admin', '84d6673c60123620923a6a612b4f6fd6f2786beb4d9e5c6ad90107aca6f79f90', 'admin@example.com');
INSERT INTO user (username, password, email) VALUES ('123456', '84d6673c60123620923a6a612b4f6fd6f2786beb4d9e5c6ad90107aca6f79f90', '123456@example.com');