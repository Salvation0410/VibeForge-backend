package com.yupi.yuaicodemother.service;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

/**
 * @author huang
 * @version 1.0
 * @description 代码下载接口
 * @date 2026/6/1
 */



public interface ProjectDownloadService {

    /**
     * 项目下载为zip
     * @param projectPath
     * @param downloadFileName
     * @param response
     * @return
     */
    public void downloadProjectAsZip(String projectPath, String downloadFileName,
                                     HttpServletResponse response);
}
