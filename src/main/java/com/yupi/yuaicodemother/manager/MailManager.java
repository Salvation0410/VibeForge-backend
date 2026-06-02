package com.yupi.yuaicodemother.manager;

import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.mail.SimpleMailMessage;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.stereotype.Component;

/**
 * 邮件发送管理器
 */
@Component
@RequiredArgsConstructor
public class MailManager {

    private final JavaMailSender javaMailSender;

    @Value("${spring.mail.username}")
    private String fromEmail;

    /**
     * 发送注册验证码邮件
     *
     * @param toEmail 收件邮箱
     * @param code 验证码
     */
    public void sendRegisterCode(String toEmail, String code) {
        SimpleMailMessage message = new SimpleMailMessage();
        message.setFrom(fromEmail);
        message.setTo(toEmail);
        message.setSubject("AI 应用生成平台邮箱注册验证码");
        message.setText(buildRegisterCodeContent(code));
        javaMailSender.send(message);
    }

    /**
     * 构建注册验证码邮件内容
     *
     * @param code 验证码
     * @return 邮件正文
     */
    private String buildRegisterCodeContent(String code) {
        return "您好，\n\n"
                + "您正在进行 AI 应用生成平台注册，验证码为：" + code + "。\n"
                + "验证码 5 分钟内有效，请勿泄露给他人。\n\n"
                + "如果不是您本人操作，请忽略此邮件。";
    }
}
