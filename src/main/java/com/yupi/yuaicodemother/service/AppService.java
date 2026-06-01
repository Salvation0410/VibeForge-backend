package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.dto.app.AppAddRequest;
import com.yupi.yuaicodemother.model.dto.app.AppAdminUpdateRequest;
import com.yupi.yuaicodemother.model.dto.app.AppQueryRequest;
import com.yupi.yuaicodemother.model.dto.app.AppUserUpdateRequest;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.AppVO;
import reactor.core.publisher.Flux;

import java.util.List;

/**
 * 应用服务层
 */
public interface AppService extends IService<App> {

    /**
     * 将应用实体转换为视图对象。
     *
     * @param app 应用实体
     * @return 应用视图对象
     */
    AppVO getAppVO(App app);


    /**
     * 构造查询条件
     * @param appQueryRequest
     * @return
     */
    public QueryWrapper getQueryWrapper(AppQueryRequest appQueryRequest);

    /**
     * 分页查询应用
     * @param appList 应用列表
     * @return
     */
    public List<AppVO> getAppVOList(List<App> appList);

    /**
     *
     * @param appId 应用id
     * @param message 用户消息
     * @param loginUser 登录用户
     * @return 流式生成的代码
     */
    public Flux<String> chatToGenCode(Long appId, String message, SysUser loginUser);

    /**
     *
     * @param appId 应用id
     * @param loginUser 登录用户
     * @return 可返回的部署地址
     */
    public String deployApp(Long appId, SysUser loginUser);

    /**
     *  异步调用网页截图
     * @param appId
     * @param appUrl
     */
    public void generateAppScreenshotAsync(Long appId, String appUrl);
}
