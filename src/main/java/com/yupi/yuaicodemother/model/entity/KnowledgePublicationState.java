package com.yupi.yuaicodemother.model.entity;
import lombok.Data;

/** epoch 是业务可见性版本，不能用租约时钟代替。 */
@Data
public class KnowledgePublicationState {
    private long epoch;
    private boolean enabled;
    private String publicationId;
}
