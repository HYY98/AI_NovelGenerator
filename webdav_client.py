"""Original WebDAV implementation shared by desktop and Web adapters."""
import os
import json
import tempfile
import shutil
import time
from xml.etree import ElementTree as ET
import requests
from requests.auth import HTTPBasicAuth

REQUEST_TIMEOUT = (5, 30)

class WebDAVClient:
    def __init__(self, base_url, username, password):
        """初始化WebDAV客户端"""
        self.base_url = base_url.rstrip('/') + '/'
        self.auth = HTTPBasicAuth(username, password)
        self.headers = {
            'User-Agent': 'Python WebDAV Client',
            'Accept': '*/*'
        }
        # WebDAV命名空间
        self.ns = {'d': 'DAV:'}

    def _get_url(self, path):
        """获取完整的资源URL"""
        return self.base_url + path.lstrip('/')

    def list_directory(self, path=""):
        """列出目录，用于连接测试。"""
        url = self._get_url(path)
        headers = self.headers.copy()
        headers['Depth'] = '1'
        response = requests.request('PROPFIND', url, headers=headers, auth=self.auth, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.content

    def directory_exists(self, path):
        """
        检查目录是否存在
        :param path: 目录路径
        :return: 布尔值，表示目录是否存在
        """
        url = self._get_url(path)
        headers = self.headers.copy()
        headers['Depth'] = '0'  # 只检查当前资源
        
        try:
            # 发送PROPFIND请求检查资源是否存在
            response = requests.request('PROPFIND', url, headers=headers, auth=self.auth, timeout=REQUEST_TIMEOUT)
            
            # 207 Multi-Status表示成功，说明资源存在
            if response.status_code == 207:
                # 解析XML响应，确认是目录
                root = ET.fromstring(response.content)
                # 查找资源类型属性
                res_type = root.find('.//d:resourcetype', namespaces=self.ns)
                # 如果包含collection元素，则是目录
                if res_type is not None and res_type.find('d:collection', namespaces=self.ns) is not None:
                    return True
            return False
        except requests.exceptions.RequestException as e:
            print(f"检查目录存在性时出错: {e}")
            return False

    def create_directory(self, path):
        """
        创建远程目录
        :param path: 要创建的目录路径
        :return: 是否创建成功
        """
        url = self._get_url(path)
        
        try:
            response = requests.request('MKCOL', url, auth=self.auth, headers=self.headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            print(f"目录创建成功: {path}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"目录创建失败: {e}")
            return False

    def ensure_directory_exists(self, path):
        """
        确保目录存在，如果不存在则创建
        :param path: 目录路径
        :return: 布尔值，表示最终目录是否存在
        """
        # 移除末尾的斜杠（如果有）
        path = path.rstrip('/')
        
        # 如果目录已经存在，直接返回True
        if self.directory_exists(path):
            print(f"目录已存在: {path}")
            return True
            
        # 递归创建父目录
        parent_dir = os.path.dirname(path)
        if parent_dir and not self.directory_exists(parent_dir):
            # 如果父目录不存在，则先创建父目录
            if not self.ensure_directory_exists(parent_dir):
                print(f"创建父目录失败: {parent_dir}")
                return False
                
        # 创建当前目录
        return self.create_directory(path)
    def upload_file(self, local_path, remote_path):
        """
        上传文件到WebDAV服务器
        :param local_path: 本地文件路径
        :param remote_path: 远程文件路径
        :return: 是否上传成功
        """
        if not os.path.isfile(local_path):
            print(f"本地文件不存在: {local_path}")
            return False

        url = self._get_url(remote_path)
        
        try:
            with open(local_path, 'rb') as f:
                response = requests.put(url, data=f, auth=self.auth, headers=self.headers, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
            
            print(f"文件上传成功: {local_path} -> {remote_path}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"文件上传失败: {e}")
            return False
    def download_file(self, remote_path, local_path):
        """
        从WebDAV服务器下载文件
        :param remote_path: 远程文件路径
        :param local_path: 本地保存路径
        :return: 是否下载成功
        """
        url = self._get_url(remote_path)
        local_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), local_path))
        self.backup(local_path)
        temp_path = None
        try:
            response = requests.get(url, auth=self.auth, headers=self.headers, stream=True, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            
            # 创建本地目录（如果需要）
            target_dir = os.path.dirname(local_path)
            os.makedirs(target_dir, exist_ok=True)
            
            fd, temp_path = tempfile.mkstemp(suffix=".tmp", dir=target_dir)
            with os.fdopen(fd, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            with open(temp_path, 'r', encoding='utf-8') as f:
                downloaded_config = json.load(f)
            required_keys = {"llm_configs", "embedding_configs", "other_params", "choose_configs"}
            if not isinstance(downloaded_config, dict) or not required_keys.issubset(downloaded_config):
                raise ValueError("下载的配置文件格式不完整")

            os.replace(temp_path, local_path)
            temp_path = None
            
            print(f"文件下载成功: {remote_path} -> {local_path}")
            return True
        except (requests.exceptions.RequestException, OSError, json.JSONDecodeError, ValueError) as e:
            print(f"文件下载失败: {e}")
            return False
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
    def backup(self, local_path):
        name_parts = os.path.basename(local_path).rsplit('.', 1)  # 只分割最后一个点
        base_name = name_parts[0]
        extension = name_parts[1]
        timestamp = time.strftime("%Y%m%d%H%M%S")
        if not os.path.exists(local_path):
            return None
        if not os.path.exists(os.path.join(os.path.dirname(local_path), "backup")):
            os.makedirs(os.path.join(os.path.dirname(local_path), "backup"))
        backup_file_name = f"{base_name}_{timestamp}_bak.{extension}"
        backup_path = os.path.join(os.path.dirname(local_path), "backup", backup_file_name)
        shutil.copy2(local_path, backup_path)
        return backup_path
