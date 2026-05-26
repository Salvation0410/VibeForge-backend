package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.dto.AppAddRequest;
import com.yupi.yuaicodemother.model.dto.AppAdminUpdateRequest;
import com.yupi.yuaicodemother.model.dto.AppQueryRequest;
import com.yupi.yuaicodemother.model.dto.AppUserUpdateRequest;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.vo.AppVO;

/**
 * 应用服务层
 */
public interface AppService extends IService<App> {

    /**
     * 用户创建应用。
     *
     * @param request 创建参数
     * @param loginUserId 当前登录用户 id
     * @return 新应用 id
     */
    Long createApp(AppAddRequest request, Long loginUserId);

    /**
     * 用户修改自己的应用。
     *
     * @param appId 应用 id
     * @param request 修改参数
     * @param loginUserId 当前登录用户 id
     * @return 是否修改成功
     */
    boolean updateMyApp(Long appId, AppUserUpdateRequest request, Long loginUserId);

    /**
     * 用户删除自己的应用。
     *
     * @param appId 应用 id
     * @param loginUserId 当前登录用户 id
     * @return 是否删除成功
     */
    boolean removeMyApp(Long appId, Long loginUserId);

    /**
     * 用户查看自己的应用详情。
     *
     * @param appId 应用 id
     * @param loginUserId 当前登录用户 id
     * @return 应用详情
     */
    AppVO getMyAppById(Long appId, Long loginUserId);

    /**
     * 用户分页查询自己的应用列表。
     *
     * @param request 查询参数
     * @param loginUserId 当前登录用户 id
     * @return 分页结果
     */
    Page<AppVO> pageMyApps(AppQueryRequest request, Long loginUserId);

    /**
     * 用户分页查询精选应用列表。
     *
     * @param request 查询参数
     * @return 分页结果
     */
    Page<AppVO> pageFeaturedApps(AppQueryRequest request);

    /**
     * 管理员删除任意应用。
     *
     * @param appId 应用 id
     * @return 是否删除成功
     */
    boolean adminRemoveApp(Long appId);

    /**
     * 管理员更新任意应用。
     *
     * @param appId 应用 id
     * @param request 更新参数
     * @return 是否更新成功
     */
    boolean adminUpdateApp(Long appId, AppAdminUpdateRequest request);

    /**
     * 管理员分页查询应用列表。
     *
     * @param request 查询参数
     * @return 分页结果
     */
    Page<AppVO> pageAdminApps(AppQueryRequest request);

    /**
     * 管理员查看应用详情。
     *
     * @param appId 应用 id
     * @return 应用详情
     */
    AppVO getAdminAppById(Long appId);

    /**
     * 将应用实体转换为视图对象。
     *
     * @param app 应用实体
     * @return 应用视图对象
     */
    AppVO getAppVO(App app);
}
