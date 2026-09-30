package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertThrows;

class CustomerServicePropertiesTest {
    @Test
    void rejectsClaimTimeoutThatCannotCoverPreparationAndPythonCall() {
        var properties = enabled();
        properties.setClaimTimeoutSeconds(40);
        assertThrows(IllegalStateException.class, properties::validate);
    }

    @Test
    void rejectsLeaseTimeoutThatCannotCoverPythonCall() {
        var properties = enabled();
        properties.setLeaseTimeoutSeconds(40);
        assertThrows(IllegalStateException.class, properties::validate);
    }

    @Test
    void rejectsAliasThatPythonWouldReject() {
        var properties = enabled();
        properties.setCollectionAlias("bad-alias");
        assertThrows(IllegalStateException.class, properties::validate);
    }

    @Test
    void rejectsAliasThatWouldOverflowPythonLeaseScope() {
        var properties = enabled();
        properties.setCollectionAlias("a".repeat(246));
        assertThrows(IllegalStateException.class, properties::validate);
    }

    private static CustomerServiceProperties enabled() {
        var properties = new CustomerServiceProperties();
        properties.setEnabled(true);
        properties.setRequestPreparationMarginSeconds(10);
        return properties;
    }
}
