# app/services/password_policy_service.py

import re


class PasswordPolicyService:
    """
    封装密码策略校验逻辑。
    """
    MIN_LEN = 8
    MAX_LEN = 16  # QQ 规则是8-16，这里设为16
    MIN_CLASS = 3
    MAX_REPEAT = 3
    MAX_SEQUENCE = 4

    def validate(self, password, username):
        """
        根据预设规则校验密码。
        :param password: 要校验的密码
        :param username: 用户名，用于检查是否包含
        :return: (bool, str) -> (是否通过, 错误信息)
        """
        # 1. 长度检查
        if not (self.MIN_LEN <= len(password) <= self.MAX_LEN):
            return False, f"密码长度必须在 {self.MIN_LEN} 到 {self.MAX_LEN} 个字符之间。"

        # 2. 字符类别检查
        classes = 0
        if re.search(r'[a-z]', password): classes += 1
        if re.search(r'[A-Z]', password): classes += 1
        if re.search(r'\d', password): classes += 1
        if re.search(r'[^a-zA-Z\d]', password): classes += 1

        if classes < self.MIN_CLASS:
            return False, f"密码必须包含大写字母、小写字母、数字、特殊符号中的至少 {self.MIN_CLASS} 种。"

        # 3. 用户名检查
        if username and username in password:
            return False, "密码中不能包含您的用户名。"

        # 4. 重复字符检查
        for i in range(len(password) - self.MAX_REPEAT):
            if len(set(password[i:i + self.MAX_REPEAT + 1])) == 1:
                return False, f"密码中不能有超过 {self.MAX_REPEAT} 个连续相同的字符。"

        # 5. 序列字符检查 (e.g., 'abcde', '98765')
        for i in range(len(password) - self.MAX_SEQUENCE):
            sub = password[i:i + self.MAX_SEQUENCE + 1]
            if self._is_sequence(sub):
                return False, f"密码中不能有超过 {self.MAX_SEQUENCE} 个连续的序列字符 (如 'abcd' 或 '4321')。"

        return True, "密码符合策略。"

    def _is_sequence(self, s):
        """ 检查字符串是否为等差序列 """
        if len(s) < 3:
            return False

        is_ord_seq = all(ord(s[i]) - ord(s[i - 1]) == 1 for i in range(1, len(s)))
        is_ord_rev_seq = all(ord(s[i - 1]) - ord(s[i]) == 1 for i in range(1, len(s)))

        return is_ord_seq or is_ord_rev_seq


# 单例
password_policy_service = PasswordPolicyService()
