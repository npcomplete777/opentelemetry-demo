// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import ProductCatalogGateway from '../gateways/rpc/ProductCatalog.gateway';
import CurrencyGateway from '../gateways/rpc/Currency.gateway';
import { Money } from '../protos/demo';

const defaultCurrencyCode = 'USD';

const ProductCatalogService = () => ({
  async getProductPrice(price: Money, currencyCode: string) {
    return !!currencyCode && currencyCode !== defaultCurrencyCode
      ? await CurrencyGateway.convert(price, currencyCode)
      : price;
  },
  async listProducts(currencyCode = 'USD') {
    const { products: productList } = await ProductCatalogGateway.listProducts();

    return Promise.all(
      productList.map(async product => {
        const priceUsd = await this.getProductPrice(product.priceUsd!, currencyCode);

        return {
          ...product,
          priceUsd,
        };
      })
    );
  },
  async getProduct(id: string, currencyCode = 'USD') {
    const product = await ProductCatalogGateway.getProduct(id);

    return {
      ...product,
      priceUsd: await this.getProductPrice(product.priceUsd!, currencyCode),
    };
  },
  /**
   * Batch fetch products by IDs using a single ListProducts gRPC call
   * instead of N individual GetProduct calls. Filters the full catalog
   * to only the requested IDs, eliminating chatty API fan-out.
   *
   * VALIS Finding: Chatty API anti-pattern detected in /api/recommendations
   * trace SBgKFM51u0iE/BIvKzWPPQ== — 4 parallel GetProduct spans replaced
   * by 1 ListProducts call.
   */
  async getProductsByIds(ids: string[], currencyCode = 'USD') {
    const { products: allProducts } = await ProductCatalogGateway.listProducts();
    const idSet = new Set(ids);
    const matchedProducts = allProducts.filter(product => idSet.has(product.id));

    return Promise.all(
      matchedProducts.map(async product => ({
        ...product,
        priceUsd: await this.getProductPrice(product.priceUsd!, currencyCode),
      }))
    );
  },
});

export default ProductCatalogService();
